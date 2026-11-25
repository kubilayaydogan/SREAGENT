# Güvenlik P1 — uygulama kanıtı

Tarih: 2026-11-25 · Kapsam: `SECURITY_IMPLEMENTATION_PLAN.md` §4 (P1.1–P1.9) · Durum: **kod + test tamam; APP_VM'e
deploy edildi ve doğrulandı (2026-11-25, §3.1).** P1.5 (DB rol ayrımı) uyumsuz değişiklik olduğu için **uygulanmadı — onay
bekliyor**; P1.4 karar 2 (TLS yok) nedeniyle ertelendi.

## 1. Özet

| # | Gereksinim | Durum | Ana dosyalar |
|---|---|---|---|
| P1.1 | Merkezi policy (AGT-01) | ✅ | `security/policy.py`, `settings/runtime.py`, `settings/connectors.py`, `services/watch_pg.py`, `worker.py`, `api/app.py` |
| P1.2 | Permission modeli (IAM-03) | ✅ | `security/permissions.py`, `api/deps.py` (`require_perm`), tüm `api/routes/*.py` |
| P1.3 | F5 kararı (ekip geneli görünürlük) | ✅ (karar a) | `features/chat/Composer.tsx` bilgilendirme; migration yok |
| P1.4 | HttpOnly cookie | ⏸ ertelendi | karar 2 (TLS yok → `Secure` cookie yapılamaz) |
| P1.5 | DB rol ayrımı | ⛔ onay bekliyor | uyumsuz değişiklik (env'de DB kullanıcıları değişir) |
| P1.6 | LLM sertleştirme (LLM-01, AGT-03) | ✅ | `airlab_agent/llm.py` (`RunBudget`), `agent/llm_port.py`, `security/llm_usage.py`, migration `0007_llm_usage`, `settings/llm_profiles.py` (allowlist) |
| P1.7 | MCP sertleştirme (MCP-01/03) | ✅ (kısmi, §4) | `airlab_agent/mcp_gateway.py`, `settings/connectors.py`, `security/policy.py`, `Connectors.tsx` |
| P1.8 | DLP (DLP-01) | ✅ | `airlab_agent/redact.py` + sink çağrı noktaları |
| P1.9 | API hataları, prompt injection (API-01, AGT-02) | ✅ | `airlab_agent/errors.py`, `api/app.py`, `airlab_agent/llm.py` (`data_json`, `bound_llm_output`), `agent/llm_port.py` |

## 2. Gereksinim bazında kanıt

### 2.1 P1.1 — Merkezi policy

- `policy.decide(action, ctx)` saf fonksiyondur ve exception fırlatmaz. Karar `ALLOW`, `DENY` veya `REQUIRE_APPROVAL` olur; her karar `reason_code` ve `policy_version` taşır. `policy_version` = `p1.1:<karara giren girdilerin sha256[:12]>` (aynı girdi → aynı sürüm).
- Fail-closed: eksik girdi ya da kural hatası `DENY policy_error` verir; tanımsız eylem `DENY unknown_action` verir.
- `run.start` kuralı eski `check_allowed` mesajlarını birebir korur. Değişen tek şey reason code'un ayrıntılanmasıdır (`agent_disabled`, `mode_scheduled_only`, `mode_manual_only`, `incident_trigger_disabled`, `unknown_trigger`).
- **Enforcement noktaları:**

  | Yol | Mekanizma | Ret davranışı |
  |---|---|---|
  | API: chat, resume, investigate, run-now | `check_allowed` → `RunNotAllowed(decision)` | Global handler 409 döner, audit `run.denied` yazar (reason_code + policy_version). `run.start` audit kaydı da `policy_version` taşır. |
  | Worker health check | aynı kapı | Audit kaydı aktör `system:scheduler` ile yazılır. |
  | Watch incident | aynı kapı | Audit `run.denied`, aktör `system:watch`; incident kaydı açık kalır. |
  | MCP gateway | `decide("tool.call")` | Ret audit `tool.denied` olarak yazılır ve gateway kullanılamaz hale gelir; rapor kısmi üretilir, sahte veri üretilmez. |

- `REQUIRE_APPROVAL` yazma veya yıkıcı yetenekli tool için üretilir. Onaylı yürütme yolu olmadığı için enforcement noktaları ALLOW dışındaki her kararı reddeder.
- Testler: `test_run_start_policy` (9 durum), `test_policy_is_fail_closed_and_versioned`, `test_tool_call_policy` (7 durum), `test_gateway_denies_and_audits_write_capable_tool`, `test_run_start_audit_carries_policy_version`. Ayrıca `test_resume_respects_agent_kill_switch` (P0) yeni reason code ile güncellendi.

### 2.2 P1.2 — Permission modeli

- 15 permission tanımlı (`record:read`, `agent:run`, `incident:write`, `approval:request`/`decide`, `settings:read`/`write`, `audit:read`, `observability:read`, `connector:manage`, `llm:manage`, `secret:manage`, `memory:delete`, `knowledge:manage`, `user:manage`).
- Rol eşlemesi eski davranışı birebir korur: viewer = okuma; sre = +ajan/incident/onay isteme/ayar okuma/audit; admin = tümü.
- 97 rotadan 49'u `require_perm` ile korunuyor; kalanlar okuma uçları ya da sahiplik kontrollü uçlar. Ret durumunda 403 döner ve audit `authz.denied` yazılır (`reason_code = missing_<perm>`).
- `GET /settings/security` matrisi döndürür (arayüz için).
- **Deny-by-default envanter testi:** `/api` altındaki her rota (97) `current_user` gerektirir. Tek istisna açık allowlist'tir: health, ready, login. Durum değiştiren her uç ya bir permission ister ya da açık listedeki sahiplik kontrollü uçlardandır (kendi konuşması / oturumu / feedback). Yeni korumasız rota eklenirse test kırılır.
- Testler: `test_every_route_is_authenticated_except_public_allowlist`, `test_write_routes_require_a_permission`, `test_role_permission_matrix_preserves_previous_behaviour`, `test_permission_denials_over_http` (rol × uç → kod ve audit reason).

### 2.3 P1.3 — Görünürlük kararı

Sohbet giriş alanının altında şu metin gösterilir: "Sorunuz ve oluşan analiz (investigation) ekipteki tüm kullanıcılara görünür; sohbet mesajları yalnız size aittir." Migration yapılmadı (karar 1).

### 2.4 P1.6 — LLM sertleştirme

- **Koşu bütçesi:** `RunBudget` `RUN_BUDGET` contextvar'ı ile koşuya bağlanır. Paylaşılan istemci süreç geneline aittir; sayaç ise koşuya aittir. `asyncio.to_thread` ve task'lar context'i kopyaladığı için eşzamanlı koşular birbirini etkilemez.
- Ayarlar `agent.llm_max_calls_per_run` (12) ve `llm_max_tokens_per_run` (400k) Settings'ten okunur.
- Sınır çağrıdan önce denetlenir; aşıldığında sağlayıcıya istek gitmez, `LlmBudgetExceeded` (bir `LlmUnavailable`) fırlatılır ve deterministik yedek devreye girer.
- Kapsanan çağrılar: route, interpret, explain ve stream answer.
- **Kullanım kaydı (`sre.llm_usage`):** LangSmith'ten bağımsızdır. Her koşu sonunda görev, model ve girdi/çıktı token'ı yazılır; sınır nedeniyle yapılmayan çağrı `denied=true` satırı olur ve audit `llm.budget_exceeded` (reason `llm_run_budget`) yazılır. Sohbet turu ve içindeki investigation tek seferde yazılır (çift kayıt yok). Rapor `budget` alanı artık süreç toplamı değil koşuya ait sayacı gösterir (önceki hata düzeltildi).
- **Model allowlist:** `SRE_LLM_MODEL_ALLOWLIST` verilmezse bilinen Claude 5 modelleri ile ortamdaki `CLAUDE_MODEL` ve `CLAUDE_RCA_MODEL` izinlidir. Kontrol iki noktada yapılır:
  - Profil oluşturma/güncellemede liste dışı model 422 döner.
  - Çalışma anında `task_profiles` liste dışı profili kullanmaz ve audit `llm.model_denied` yazar.
  - Arayüz model seçimi zaten sabit listeye bağlıydı.
- Testler (agent + platform): `test_run_budget_blocks_calls_before_they_are_made` (gerçek `Llm`/`ClaudePort` kodu, ağsız stub ile; reddedilen çağrıda stub sayacı artmaz), `test_run_budget_is_isolated_per_run`, `test_usage_persisted_and_budget_denial_audited`, `test_model_allowlist`, `test_runtime_skips_non_allowlisted_profile`, `test_allowed_models_defaults_and_override`, migration up/down testi (0007).

### 2.5 P1.7 — MCP sertleştirme

- **Tool tanım bütünlüğü:**
  - Keşifte her tool için `sha256(ad + açıklama + inputSchema + annotations)` saklanır.
  - Onaylı tool'un ilk özeti TOFU ile kabul edilir. Sonraki keşifte özet farklıysa tool `needs_review` olur ve audit `connector.tool_changed` yazılır.
  - Policy bu tool için `DENY tool_definition_changed` verir; gateway kullanılamaz.
  - Yönetici tool listesini yeniden kaydettiğinde (onay) o anki özetler onaylanır ve bayrak kalkar.
  - Arayüz bayrağı connector listesinde ve tool diyaloğunda gösterir.
- **Çalışma anı doğrulaması:** her MCP oturumu açılışında `list_tools` ile sunucunun bildirdiği `execute-dql` tanımı onaylı özetle karşılaştırılır. Fark ya da tool yokluğu `McpUnavailable` üretir (rug-pull koruması; keşif yapılmasa da yakalanır).
- **Çağrı anı DQL doğrulaması:** boş sorgu, 8000 karakter üstü, kontrol karakteri içeren ya da `fetch`/`timeseries` dışında bir komutla başlayan sorgu sunucuya **gönderilmez** (`ERROR`, query bütçesinden düşmez). Katalogdaki ve platformdaki (correlation, telemetry) tüm sorguların doğrulayıcıdan geçtiği test edilir (yanlış ret = sessiz veri kaybı).
- **Yanıt sınırı:**
  - 4 MB üstü metin `ERROR` olarak işaretlenir.
  - 5000 kayıt üstü yanıt kırpılır ve kayda bir not eklenir.
  - `records` liste değilse `ERROR` döner.
- Testler: `test_validate_dql` (9 durum), `test_tool_hash_detects_any_definition_change`, `test_rejected_dql_is_never_sent`, `test_response_size_and_record_limits`, `test_tool_integrity_checked_per_session`, `test_every_catalog_query_passes_validation`, `test_platform_queries_pass_dql_validation`, `test_tool_definition_change_requires_review` (TOFU → değişiklik → DENY + audit → yeniden onay → ALLOW; gateway özet taşır).

### 2.6 P1.8 — DLP

- **Secret kalıpları** her sink'te her zaman maskelenir. Mevcut kalıplara şunlar eklendi: JWT, `Bearer <token>`, Slack webhook, PEM private key, AWS erişim anahtarı.
- **PII kalıpları:**
  - e-posta;
  - IBAN (mod-97);
  - kart numarası (Luhn + IIN: Visa, MC, Amex, Discover);
  - TC kimlik no (resmî checksum).
- Sayısal kalıplar checksum ile doğrulandığı için telemetrideki zaman damgası, ID, port ve 11 haneli sayaçlar maskelenmez (8 negatif fixture ile test edildi).
- **Sink politikası** (`dlp_policy()`, `GET /settings/security` içinde `dlp`):
  - Sink'ler: `llm`, `trace`, `log`, `notify`, `export`, `storage`, `audit`.
  - Varsayılan olarak PII tüm sink'lerde maskelenir.
  - `SRE_DLP_PII_KEEP_SINKS` yalnız adı verilen sink'te PII'yı korur.
  - Bilinmeyen sink adı maskelenir (fail-closed).
- Çağrı noktaları:

  | Sink | Nerede uygulanır |
  |---|---|
  | `llm` | Prompt veri blokları; kullanıcı sorusu ve geçmiş (route, interpret, explain, cevap) |
  | `trace` | LangSmith |
  | `log` | JSON log |
  | `notify` | Slack özeti ve hata mesajı |
  | `export` | RCA markdown |
  | `audit` | Audit `detail` / `before` / `after` |
  | `storage` | Rapor ve kayıt (varsayılan) |

- Testler: `test_secrets_masked_on_every_sink` (6), `test_pii_masked_by_default` (6), `test_no_false_positives_on_telemetry_like_values` (8), `test_nested_structures_and_sink_override`, `test_keep_sinks_env_parsing`, `test_dlp_applied_at_platform_sinks` (LLM prompt, Slack, log, audit gerçek kod yolları).

### 2.7 P1.9 — API hataları ve prompt injection

- **Hata sızıntısı:** yalnız `UserError` mesajı istemciye aynen gider. Kasıtlı doğrulama mesajları `UserError`'a çevrildi; `UnsafeUrl` ve `SecretError` de `UserError` alt sınıfıdır. Diğer `ValueError`'lar genel bir mesaj döndürür, ayrıntısı log'a yazılır. `IntegrityError` yanıtındaki DB mesajı (constraint/kolon/değer) kaldırıldı ve yalnız log'a yazılıyor.
- **LLM çıktısı:** `AnalysisRequest` ve `ChatRoute` `extra="forbid"` ile tanımlı; şema dışı alan parse hatasına, parse hatası da deterministik yedeğe yol açar. Değerler şemaya değil after-validator'a konan sınırlarla kırpılır: dakika 1–43200, gün 1–365, en fazla 10 uygulama × 64 karakter, arama terimi 200 karakter. Bu sayede sağlayıcının şema desteğine bağımlılık oluşmaz.
- **Veri bloğu kaçışı:** LLM'e giden veri JSON'unda `<` ve `>` `<`/`>` olarak yazılır. Telemetri metni `</analysis_data>` ile bloğu kapatıp talimat bölgesine taşamaz; JSON anlamı değişmez.
- Mevcut kontroller doğrulandı: LLM'in araç erişimi yok. `focus_apps` bilinen uygulamalarla filtreleniyor. `search_terms` yalnız parametreli `websearch_to_tsquery`'ye gidiyor (DQL'e ya da ham SQL'e girmez).
- Testler: `test_untrusted_text_cannot_close_the_data_block` (4 injection fixture), `test_llm_output_rejects_unknown_fields`, `test_llm_output_values_are_bounded`, `test_prompt_data_block_cannot_be_closed_by_telemetry`, `test_chat_route_output_is_strict`, `test_error_responses_do_not_leak_internals`.

## 3. Test sonuçları (gerçek çıktılar, 2026-11-25, APP_VM test konteynerleri)

| Paket | Komut | Sonuç |
|---|---|---|
| Backend (PostgreSQL 17, geçici DB) | `pytest tests -q` | **119 passed, 1 skipped** (P1 dosyası: 21 test fonksiyonu, parametrelerle 35 durum) |
| sre-agent | `pytest tests -q` (`--network none`) | **126 passed** (yeni: `test_mcp_security`, `test_dlp`, `test_injection` → 42) |
| Frontend | `tsc --noEmit`, `vitest run`, `vite build` | **TSC_OK, 38 passed, build ✓** |
| Secret taraması | `gitleaks dir` (çalışma ağacı, `.gitleaks.toml`) | **no leaks found** (test fixture'ları kaynakta parçalı) |

### 3.1 Canlı deploy doğrulaması (APP_VM, 2026-11-25 18:40 UTC)

| Kontrol | Sonuç |
|---|---|
| Önkoşul: aktif chaos deneyi | `active: None` |
| Rollback imajları | `airlab/sre-platform-{backend,frontend}:pre-p1` etiketlendi |
| Migration | `alembic_version = 0007`, `sre.llm_usage` mevcut |
| Servisler | `sre-backend`, `sre-worker`, `sre-frontend` healthy; worker/backend log'unda hata yok |
| Kimliksiz erişim | `/api/settings/security` 401, `/api/agent/runs` 401, `/api/docs` 404, `/api/health` 200 |
| Arayüz paketi | Sohbet görünürlük bilgisi `InvestigatePage-*.js`, tool inceleme uyarısı `SettingsPage-*.js` içinde |
| Canlı süreç | `agent:run` → admin, sre; DLP llm sink `{secrets: true, pii: true}`; örnek metin → `[email] kart [card]`; bütçe varsayılanları 12 / 400000 |

Regresyon notları:
- P0 testinde iki beklenti yeni reason code'lara güncellendi: `policy_agent_settings` → `agent_disabled` ve `requires_admin` → `missing_user:manage`. Davranış (409/403 ve audit kaydı) aynı kaldı.
- Migration testi head `0007`'ye ve `0007 → 0006` downgrade'ine genişletildi.

## 4. Plandan sapmalar ve kalan riskler

| # | Konu | Durum / gerekçe |
|---|---|---|
| D1 | Model allowlist ve DLP sink politikası Settings yerine env (`SRE_LLM_MODEL_ALLOWLIST`, `SRE_DLP_PII_KEEP_SINKS`) | Güvenlik kontrolünün arayüzden gevşetilmesini zorlaştırır; görünürlük `GET /settings/security` ile sağlanır. İstenirse Settings bölümüne taşınabilir (revizyon + audit). |
| D2 | P1.7 DQL doğrulaması: zaman aralığı ≤ `max_minutes` ve "katalog anahtarından geliyor mu" kontrolleri **yapılmadı** | Pencere zaten `resolve_window` ile sınırlanıyor; sorgular yalnız kod kataloğundan üretiliyor (LLM metni DQL'e girmiyor, test edildi). Komut allowlist'i + uzunluk + kontrol karakteri uygulandı. |
| D3 | Yanıt sınırları sabit (4 MB / 5000 kayıt), Settings bütçesi değil | Telemetri boyutları bu sınırların altında; gerekirse ayara taşınır. |
| D4 | Tool özeti TOFU | İlk keşif anında sunucu zaten kötü niyetliyse kabul edilir; yönetici ilk onayda açıklamaları görür (diyalog). |
| D5 | Token sınırı çağrı öncesi denetlenir | Tek çağrı kendi `max_tokens`'ı kadar aşabilir (sağlayıcı sınırı). |
| D6 | IPv4 maskeleme yapılmadı (plan: opsiyonel) | IP/host analiz için temel telemetri; iç ağ adresleri zaten repoda/dokümanda. |
| D7 | Prompt şablon sürümü raporda yok (P1.6 maddesi) | Kalan iş (P2). |
| D8 | Gerçek LLM ile injection eval'i çalıştırılmadı | Maliyetli ve onay gerektirir; mock'lu testler + yapısal kontroller (araç yok, şema sıkı, veri kaçışı) uygulandı. |
| D9 | Dynatrace token scope operasyonel doğrulaması | Dokümante kontrol listesi kalan iş; token değişikliği kullanıcı onayıyla yapılır. |
| R1 | P1.5 uygulanmadı | Uygulama DB kullanıcısı hâlâ owner: audit append-only tetikleyicileri owner tarafından devre dışı bırakılabilir (P0 R-kaydı geçerli). |
| R2 | PII artık `storage` sink'inde de maskeli | Kullanıcı sorusundaki e-posta kayıtlı mesajda `[email]` görünür. Bilinçli varsayılan; `SRE_DLP_PII_KEEP_SINKS=storage` ile geri alınabilir. |

## 5. Deploy ve rollback

1. Önkoşul: `curl 172.20.229.26:8080/api/v2/experiments` → `.active == None`.
2. Kodu `/opt/airlab`'a kopyala, `docker compose --profile platform build sre-migrate sre-frontend`, `run --rm sre-migrate` (0006 → 0007), `up -d sre-backend sre-worker sre-frontend`.
3. Doğrulama:
   - `alembic current = 0007`;
   - `GET /settings/security` içinde `permissions` ve `dlp` alanları;
   - bir sohbet turu sonrası `sre.llm_usage` satırları;
   - viewer ile `POST /agent/chat` → 403 ve audit `missing_agent:run`;
   - connector keşfi sonrası `approved_hashes` dolu.
4. Rollback: `sre-platform migrate --downgrade 0006` (yalnız `llm_usage` silinir) + `:pre-p1` imajlarını `0.1.0` olarak yeniden etiketleyip `up -d`. Tool özetleri connector `capabilities` JSON'unda; eski kod bu alanları yok sayar.

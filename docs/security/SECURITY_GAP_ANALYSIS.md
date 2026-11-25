# Security Gap Analysis

Kaynak gereksinim: [`SECURITY_IMPLEMENTATION.md`](SECURITY_IMPLEMENTATION.md). Uygulama planı:
[`SECURITY_IMPLEMENTATION_PLAN.md`](SECURITY_IMPLEMENTATION_PLAN.md).

Tarih: 2026-11-25 · Kapsam: `main` dalı, commit `31bfd85` + commit edilmemiş episodic/working memory değişiklikleri.

## 0. Yöntem ve sınırlar

- Durumlar **kaynak kodda gösterilebilen enforcement**'a göre verildi. Bir kontrol ancak backend/runtime'da çalışan
  kodla (ve mümkünse mevcut bir testle) gösterilebiliyorsa `implemented` sayıldı. Yorum, prompt talimatı, arayüz
  gizlemesi ya da dokümantasyon tek başına kanıt sayılmadı.
- Statik inceleme: Python backend (`sre-platform/backend`), ajan çekirdeği (`sre-agent/src/airlab_agent`), React
  frontend (`sre-platform/frontend`), `app-vm/docker-compose.yml`, Dockerfile'lar, nginx yapılandırması,
  `.gitignore`, bağımlılık kilitleri.
- **Çalıştırılmayanlar:** Semgrep, Bandit, Gitleaks, Trivy, pip-audit, npm audit, ZAP. Bu araçlar yerel ortamda
  kurulu değil; hiçbir tarama sonucu iddia edilmez. Canlı penetrasyon testi yapılmadı.
- Gerçek secret değerleri okunmadı/yazdırılmadı. Token biçimine benzeyen dizeler yalnız dosya adıyla raporlandı.
- Mevcut test kanıtı: backend 71 geçti / 1 atlandı (gerçek PostgreSQL), sre-agent 84 geçti, frontend 37 geçti
  (2026-11-25). Bu testler güvenlik kontrollerinin bir kısmını kapsar; kapsananlar aşağıda belirtildi.

Durum değerleri: **implemented** (enforce ediliyor + kanıt var) · **partial** (bir kısmı enforce ediliyor) ·
**missing** · **not applicable** (gerekçesiyle).

## 1. Sistem özeti ve güven sınırları

```
Tarayıcı ──HTTP:8090──▶ sre-frontend (nginx, SPA + /api reverse proxy)
                           │  sre-web ağı
                           ▼
                    sre-backend (FastAPI, uvicorn, JWT bearer) ──┐
                           │  sre-data ağı (internal: true)       │ airlab-agent ağı (dışa çıkış)
                           ▼                                       ├──▶ Dynatrace Remote MCP (HTTPS, execute-dql)
                    sre-postgres (sre + langgraph şemaları)        ├──▶ Anthropic API (HTTPS)
                           ▲                                       ├──▶ LangSmith (HTTPS, tracing + sync)
                    sre-worker (scheduler: watch, health check,    └──▶ Slack webhook (HTTPS)
                      retention, langsmith-sync)
```

| Sınır | Gözlem |
|---|---|
| Kullanıcı ↔ platform | Yalnız HTTP (nginx `listen 80`, compose `"${SRE_UI_PORT:-8090}:80"` tüm arayüzlere yayımlı). TLS yok. |
| Kimlik | Yerel kullanıcılar, scrypt parola özeti, HS256 JWT (8 saat), `localStorage`'da tutulur. SSO/MFA yok. |
| Yetki modeli | 3 rol (`viewer` < `sre` < `admin`), rütbe karşılaştırması (`api/deps.py: require`). Rol her istekte DB'den okunur. |
| Tenancy | **Tek organizasyon.** Tenant/organization alanı yok. Konuşmalar ve working memory kullanıcıya özel; investigation, incident, RCA, runbook, known issue, episode kayıtları tüm kimliği doğrulanmış kullanıcılara açık. |
| Agent → telemetri | Claude'a tool bağlanmaz (ADR-007). DQL'ler sabit katalogdan (`airlab_agent/signals.py`) üretilir; gateway yalnız `execute-dql` çağırır (`mcp_gateway.McpGateway._one_inner`). |
| Agent → LLM | Toplulaştırılmış rapor (`prompts.analysis_payload`) `<..._data>` blokları içinde, regex redaction sonrası gönderilir. |
| Kalıcılık | Tek DB rolü (`SRE_DB_USER`) migrate, backend ve worker için ortak; şema sahibi. DB bağlantısı TLS'siz, yalnız internal Docker ağı. |

**Gerçek izolasyon sınırı:** organizasyon = deployment. Çok kiracılı (multi-tenant) kullanım desteklenmiyor; bu
belge SaaS tenancy önermez.

## 2. Özet tablo

> Bu tablo **P0 öncesi** (2026-11-25 başlangıç) durumdur. P0 sonrası değişen durumlar §2.1'de; kanıt:
> [SECURITY_P0_EVIDENCE.md](SECURITY_P0_EVIDENCE.md).

| ID | Başlık | Durum | En önemli açık |
|---|---|---|---|
| IAM-01 | Authentication | partial | TLS yok; SSO/MFA yok; parola sıfırlama yok; ilk admin env parolasıyla |
| IAM-02 | Session lifecycle | partial | Token `localStorage`'da; sunucu tarafı logout/revocation yok; oturum listesi yok |
| IAM-03 | Authorization | partial | Kaba RBAC; permission modeli yok; kayıtlar org genelinde; service account yok |
| AGT-01 | Central policy enforcement | missing | Politika motoru ve karar logu yok; kontroller dağınık |
| AGT-02 | Prompt injection | partial | Yapısal savunma güçlü (tool yok, veri sınırlı); injection testi yok |
| AGT-03 | Execution limits | partial | Çoğu limit var; per-run token/LLM çağrı limiti yok; kill switch aktif koşuyu durdurmaz; resume politika kontrolünü atlıyor |
| AGT-04 | Human-in-the-loop | partial | Read-only by design; approval dört göz + süre; argüman bağlama yok (yürütme yolu da yok) |
| MCP-01 | Trusted connector registry | partial | Admin-only kayıt, tool allowlist; schema/description değişikliği tespiti yok |
| MCP-02 | Network/protocol | partial | SSRF guard yalnız kayıt/test anında; çalışma anında ve ağ seviyesinde egress kontrolü yok |
| MCP-03 | Dynatrace/DQL | partial | Şablonlu DQL; çağrı anında argüman/zaman/çıktı boyutu doğrulaması yok; provider scope doğrulanmadı |
| LLM-01 | Provider management | partial | Yalnız Anthropic; per-run token/maliyet sınırı yok; bütçe LangSmith verisine bağlı |
| DLP-01 | Data minimization/redaction | partial | Regex redaction + toplulaştırılmış payload; sınıflandırma, PII tespiti, sink başına kural yok |
| SEC-01 | Secret management | partial | Fernet deposu + env referansı; VM'de anahtar tanımlı değil; rotasyon/expiry yok |
| SEC-02 | Encryption/retention | missing | UI/DB TLS yok; at-rest şifreleme kanıtı yok; anahtar yaşam döngüsü yok |
| MEM-01 | State/memory isolation | partial | Konuşma/thread sahiplik kontrolü var; checkpoint/Store retention yok; DB seviyesinde ayrım yok |
| AUD-01 | Audit events | partial | Yalnız Settings modülü; auth, kullanıcı, run, approval, memory olayları yok; DB seviyesinde append-only değil |
| AUD-02 | SIEM/alerting | missing | Güvenlik olayı akışı, SIEM çıkışı, alarm yok |
| API-01 | Web/API security | partial | Şema doğrulama, ORM, CSP vb. var; HSTS yok, `/api/docs` açık, genel rate limit yok, hata mesajı sızıntısı riski |
| INF-01 | Deployment hardening | partial | Backend non-root, PG ağı internal; frontend root, cap_drop/read_only yok; UI portu tüm arayüzlerde; backup yok |
| UI-01 | Security & Governance UI | partial | Settings > Security & Audit (secret metadata, roller, audit log) var; ayrı modül ve ekranların çoğu yok |
| SDLC-01 | DevSecOps | missing | CI yok; SAST/secret/SCA/SBOM yok; `SECURITY.md` yok |

### 2.1 P0 sonrası (kod + test; APP_VM deploy onay bekliyor)

| Bulgu | Yeni durum |
|---|---|
| F1 TLS | Risk kabulü (karar 2); telafi: idle oturum, sunucu iptali, kalıcı throttle, audit, bind IP ayarı |
| F2 Oturum | **Giderildi (sunucu tarafı):** `auth_sessions`, logout, iptal, idle 30 dk, parola/rol değişikliğinde iptal, SSE yeniden doğrulama. `localStorage` kalır (R2) |
| F3 Audit kapsamı | **Giderildi:** auth, kullanıcı, run, approval, memory, export, egress olayları; UPDATE/DELETE/TRUNCATE tetikleyicisi. Sahip rol tetikleyiciyi kaldırabilir (P1.5) |
| F4 Resume policy | **Giderildi** |
| F7 SSRF çalışma anı | **Giderildi** (MCP, LLM base_url, Slack); ağ seviyesi egress P2.6 |
| F9 CI | **Workflow hazır**, ilk koşu push bekliyor |
| F10 Container | **Giderildi** (non-root frontend, cap_drop, no-new-privileges, read_only) — deploy bekliyor |
| F11 API docs | **Giderildi** (varsayılan kapalı) |
| F13 Throttle | **Giderildi** (kalıcı login throttle, güvenilir proxy IP, kullanıcı başına çalıştırma hız sınırı) |
| F14 Secret deposu | **Giderildi** (VM'de etkin) |
| F18 Test fixture'ları | Sentetik olduğu doğrulandı; `.gitleaks.toml` dar allowlist |

Gereksinim durumu (P0 sonrası): IAM-02 partial (cookie/UI eksik), AUD-01 partial (DB rol ayrımı eksik),
SDLC-01 partial (CI hazır, koşmadı), INF-01 partial (DB rolleri, backup eksik), MCP-02 partial (ağ egress eksik).
P0 hiçbir gereksinimi tek başına `implemented` yapmaz; kalan maddeler P1–P2'dedir.

## 3. Bulgular — öncelik sırasıyla

| # | Bulgu | Kanıt | Risk | Öncelik |
|---|---|---|---|---|
| F1 | UI ve API yalnız HTTP; port 8090 tüm arayüzlerde | `sre-platform/frontend/nginx.conf` (`listen 80`), `app-vm/docker-compose.yml` `sre-frontend.ports` | Parola ve bearer token ağda açık metin; oturum çalma | Risk kabulü (karar 2); telafi: plan §11.1 |
| F2 | Token `localStorage`'da, 8 saat geçerli, sunucu tarafı iptal yok | `frontend/src/services/api.ts: tokenStore`, `core/security.py: create_token` (`jti` saklanmıyor), `core/config.py jwt_ttl_minutes=480` | XSS veya cihaz erişiminde token 8 saat kullanılabilir; parola değişikliği eski token'ı iptal etmez | P0 |
| F3 | Audit yalnız Settings modülünü kapsıyor; DB seviyesinde değiştirilebilir | `settings/service.py: audit` yalnız `api/routes/settings.py`'den çağrılıyor; login, `/users`, approvals, runs, memory/focus için kayıt yok; aynı DB rolü `audit_events` tablosunun sahibi | Yetki değişikliği, başarısız giriş, run iptali iz bırakmaz; kayıt silinebilir | P0 |
| F4 | `POST /conversations/{id}/resume` `check_allowed` çağırmıyor | `api/routes/agent.py: resume` (chat ve investigate 104/149. satırlarda çağırıyor) | Agent devre dışı/`scheduled` modundayken yarım kalan tur sürdürülebilir (LLM çağrısı yapılabilir) | P0 |
| F5 | Sohbetten doğan investigation'lar (soru + açıklama) tüm kullanıcılara açık | `api/routes/records.py: list_investigations/get_investigation` yalnız `current_user`; `agent/chat.py: investigate` başlığa soruyu yazar | Kullanıcının sorusu/yorumu org içinde başka kullanıcılara görünür; episode'lar da bunu taşır | Kabul edildi — ekip geneli (karar 1) |
| F6 | Tek DB rolü, şema sahibi; DB TLS yok | `core/config.py` (`SRE_DB_USER`), `db/session.py: make_engine` (sslmode yok), compose `sre-migrate/sre-backend/sre-worker` aynı env | Uygulama ele geçirilirse audit/checkpoint dahil her tablo değiştirilebilir | P0 |
| F7 | SSRF kontrolü yalnız kayıt/test anında | `settings/netguard.py: check_url` çağrıları `api/routes/settings.py` ve `settings/connectors.py: test`; `connectors.gateway_factory` ve `services/slack_notify.py` çalışma anında çağırmıyor | Kayıttan sonra DNS değişimi (rebinding) ile iç ağa istek; ağ seviyesinde egress kısıtı yok | P0 |
| F8 | Merkezi policy motoru yok | Kontroller dağınık: `deps.require`, `settings/runtime.check_allowed`, `connectors.gateway_factory`, `McpGateway` bütçesi | Yeni giriş noktaları kontrolü unutabilir; karar/policy sürümü loglanmıyor; fail-closed tanımsız | P1 |
| F9 | CI ve güvenlik taraması yok | Depoda `.github/`, `.gitlab-ci.yml` vb. yok; `SECURITY.md` yok | Secret sızıntısı, zafiyetli bağımlılık fark edilmez | P0 (temel), P3 (SBOM/provenance) |
| F10 | Frontend container root; platform container'larında `cap_drop`, `no-new-privileges`, `read_only` yok | `frontend/Dockerfile` (`nginx:1.29-alpine`, `USER` yok); compose `sre-*` servisleri | Container kaçışında etki alanı büyür | P0 |
| F11 | `/api/docs` ve `/api/openapi.json` kimliksiz açık | `api/app.py: FastAPI(docs_url="/api/docs", openapi_url=...)` | Saldırı yüzeyi keşfi kolaylaşır (düşük) | P0 (düşük maliyet) |
| F12 | `ValueError` mesajı istemciye aynen dönüyor | `api/app.py: _ve` → `str(e)` | İç ayrıntı sızıntısı (düşük) | P1 |
| F13 | Login throttle süreç belleğinde; diğer uçlarda genel rate limit yok | `api/routes/auth.py: _FAILS` (restart'ta sıfırlanır); Settings test uçları 5 sn sınırı | Parola deneme sınırı restart ile aşılır; pahalı uçlar (chat, investigate) yalnız eşzamanlılıkla sınırlı | P0 |
| F14 | VM'de `SRE_SETTINGS_KEY` yok; secret'lar env dosyalarında | Smoke test: `secret_store=False`; `.gitignore` `deploy/out/`, `app-vm/*.env` | **2026-11-25 giderildi:** anahtar VM'de etkin. Önceden: arayüzden secret yönetimi kapalı; yerel `deploy/out/` içinde düz metin env dosyaları (repo dışı ama diskte) | P0 (operasyon) |
| F15 | LLM aylık bütçesi LangSmith senkron verisine bağlı | `settings/llm_profiles.py: month_cost` (`LlmRun` tablosu) | Tracing kapalıysa ya da senkron gecikirse bütçe uygulanmaz; per-run token tavanı yok | P1 |
| F16 | MCP tool read-only kontrolü sunucu ipuçlarına dayanıyor | `settings/connectors.py` (`readOnlyHint`/`destructiveHint`) | Kötü niyetli/hatalı sunucu ipucunu yanlış bildirebilir; schema/description değişikliği tespit edilmiyor | P1 |
| F17 | Redaction yalnız regex + bilinen secret değerleri | `airlab_agent/redact.py` (`_PATTERNS`, `register_secrets`) | E-posta, kişi adı, IBAN, T.C. kimlik no gibi kişisel veri log/trace içinden LLM/LangSmith/Slack'e gidebilir | P1 |
| F18 | Token biçimli test fixture'ları | `backend/tests/test_langsmith_sync.py`, `test_persistence.py`, `test_slack_notify.py` (sentetik görünüyor) | Secret tarayıcıları yanlış pozitif verir; allowlist gerekir | P0 (CI ile) |

## 4. Gereksinim bazında ayrıntı

### IAM-01 Authentication — partial

| Alt gereksinim | Kanıt | Durum | Risk | Önerilen değişiklik | Test |
|---|---|---|---|---|---|
| Olgun kütüphane, özel kripto yok | `core/security.py`: `hashlib.scrypt` (N=2^14, r=8, p=1), `pyjwt` HS256 | implemented | — | Argon2id'ye geçiş zorunlu değil; scrypt onaylı adaptif KDF. Parametreler yapılandırılabilir olmalı | Birim: hash/verify, eski özet uyumluluğu |
| OIDC SSO (issuer, audience, nonce, PKCE) | Yok | missing | Kurumsal kimlik yaşam döngüsü (işten ayrılma) yansımaz | `authlib` ile OIDC code flow + PKCE; mevcut yerel giriş break-glass olarak kalır | Sahte IdP ile imza/issuer/audience/nonce negatif testleri |
| Parola politikası | `hash_password`: min 10; `UserCreateIn.password min_length=10` | partial | Zayıf ama uzun parolalar | Yapılandırılabilir politika (uzunluk, sızmış parola listesi opsiyonel) | Birim |
| Throttling, enumeration direnci | `auth.py: _throttled` (kullanıcı+IP, 5/5 dk), `users.authenticate` DUMMY_HASH ile sabit süre, aynı hata mesajı | partial | Bellek içi; restart sıfırlar; IP `request.client.host` (nginx arkasında proxy IP'si olabilir) | DB/kalıcı sayaç, trusted proxy ile gerçek IP, kademeli gecikme | Entegrasyon: 6. deneme 429, restart sonrası da |
| Güvenli parola sıfırlama | Yok (yalnız admin `PATCH /users/{id}` ile parola atar) | missing | Kendi parolasını değiştiremez; admin parolaları bilir | Self-service parola değişikliği (eski parola ile) + admin tek kullanımlık reset token | Negatif: süresi dolmuş/yeniden kullanılmış token |
| MFA | Yok | missing | Tek faktör | IdP üzerinden (P2); yerel TOTP opsiyonel | IdP politikası testi |
| İlk admin | `users.ensure_admin`: admin yoksa `SRE_ADMIN_PASSWORD` ile oluşturur; yoksa açılış hatası | partial | Env dosyasında kalıcı parola; ilk girişte değişim zorunlu değil | İlk girişte parola değişimi zorunlu; env parolasının kaldırılması dokümante | Entegrasyon |
| Varsayılan ortak kimlik yok | Kodda sabit parola yok; `.env.example` placeholder | implemented | — | — | — |
| TLS | Yok (F1) | missing | Parola açık metin | P0: TLS termination | Bağlantı testi |

### IAM-02 Session lifecycle — partial

| Alt gereksinim | Kanıt | Durum | Risk | Önerilen değişiklik | Test |
|---|---|---|---|---|---|
| HttpOnly/SameSite cookie veya güvenli saklama | `services/api.ts: tokenStore` → `localStorage` | missing | XSS halinde token çalınır (CSP `script-src 'self'` riski azaltır ama ortadan kaldırmaz) | Kısa ömürlü access token + HttpOnly/SameSite=Strict refresh cookie (CSRF koruması ile) **veya** en azından kısa TTL + sunucu tarafı oturum | E2E |
| Token süresi | `exp` zorunlu (`decode_token`), 480 dk | partial | Uzun | Idle (ör. 30 dk) + mutlak (ör. 8 sa) | T04 |
| Logout / revocation | Frontend yalnız `tokenStore.clear()`; `jti` üretiliyor ama saklanmıyor | missing | Çalınan token iptal edilemez | `sessions` tablosu (jti, user, created, last_seen, revoked_at); her istekte kontrol; logout ve admin iptali | T04: iptal edilen token 401 |
| Kullanıcı devre dışı / rol değişimi | `deps.current_user`: her istekte `is_active` ve DB'deki rol | implemented | — | Parola değişiminde tüm oturumları iptal et | Entegrasyon |
| Aktif oturum listesi | Yok | missing | — | `GET /auth/sessions`, `DELETE /auth/sessions/{id}` | API testi |
| CSRF | Bearer header kullanıldığı için cookie tabanlı CSRF yüzeyi yok | not applicable (bugün) | Cookie'ye geçilirse gerekli | Cookie geçişiyle birlikte double-submit/`SameSite=Strict` | — |

### IAM-03 Authorization — partial

| Alt gereksinim | Kanıt | Durum | Risk | Önerilen değişiklik | Test |
|---|---|---|---|---|---|
| Deny-by-default, sunucu tarafı RBAC | Tüm rotalarda `current_user`/`sre`/`admin` bağımlılığı (rota taraması: `health`, `ready`, `auth/login` dışında kimliksiz rota yok); `test_api.py: test_health_auth_rbac` | implemented (kaba) | Yeni rota bağımlılık eklemeyi unutabilir; varsayılan router seviyesinde değil | Router seviyesinde varsayılan bağımlılık + "kimliksiz rota" listesi testi | Rota envanteri testi |
| Permission modeli (`agent:run` …) | Yok; 3 rol rütbesi | missing | En az yetki uygulanamaz (ör. connector test ≠ yönetim) | Rol → permission eşlemesi; mevcut roller birebir eşlenir (yetki genişlemesi yok) | Matris testi |
| Kaynak seviyesi yetki | Konuşma, mesaj, working memory: `conversations.get(s, id, u.id)`; run/SSE: `_own_run` (sahip veya admin); approval iptali: yalnız açan | partial | Investigation/RCA/incident/episode org genelinde açık (F5) | Karar: org genelinde paylaşım (dokümante) veya sohbet kaynaklı investigation'lar için sahip+rol filtresi | T03 |
| Kimlik güvenilir kaynaktan | `user_id` JWT `sub` + DB; istemciden user_id alınmıyor | implemented | — | — | — |
| Kendi yetkisini yükseltme | `update_user`: kendi rolünü/aktifliğini değiştiremez; yalnız admin rol atar | partial | Admin başka bir kullanıcıyı admin yapabilir (tek admin rolü, ayrım yok) | Security Admin / Platform Admin ayrımı; rol değişikliği audit | Negatif test |
| Job/worker kimliği | Worker DB'ye doğrudan bağlanır; zamanlanmış investigation `user_id=None`; `check_allowed("scheduled"/"incident")` | partial | Scoped service identity yok; worker tüm tablolara erişebilir | Worker için ayrı DB rolü ve "system:scheduler" aktörü; audit'te actor_type | — |
| Service account | Yok | missing | Entegrasyonlar için kişisel hesap kullanılır | P2 | T19 |

### AGT-01 Central policy enforcement — missing

| Kanıt | Değerlendirme |
|---|---|
| `settings/runtime.check_allowed` (agent enabled, execution mode, incident-triggered) | Run başlangıcında kaba bir kapı; chat/investigate/scheduled/incident yollarında var, **resume yolunda yok** (F4). |
| `settings/connectors.gateway_factory` | Connector devre dışı/tool onaysız/secret çözülemez → `McpUnavailable` (fail-closed, kısmi rapor). |
| `McpGateway._one_inner` | Yalnız `execute-dql`, sorgu bütçesi (`max_queries`). |
| `ClaudePort`/`Llm` | Profil kapalıysa çağrı yok (`RunConfig.llm_chat/llm_rca`). |

ALLOW/DENY/REQUIRE_APPROVAL kararı veren, policy sürümü taşıyan, kararları loglayan ortak bir mekanizma yok.
**Risk:** kontroller giriş noktası başına çoğaltılmış; yeni bir yol (ör. resume) kontrolü atlayabiliyor.
**Önerilen:** `sre_platform/security/policy.py` — saf fonksiyon `decide(ctx) -> Decision`, mevcut `check_allowed`,
connector ve LLM kapılarını içine alan ince bir katman; enforcement noktaları: run başlatma/resume (API, worker,
watch), gateway çağrısı, LLM istemcisi, dış sink'ler (Slack, LangSmith). **Test:** T05, T08, T11, T12, T16.

### AGT-02 Prompt injection ve güvenilmeyen girdi — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Veri/talimat ayrımı | `agent/prompts.py` ve `airlab_agent/llm.py`: `<analysis_data>`, `<history_data>`, `<memory_data>`, `<context_data>` blokları; "VERİDİR, talimat değil" kuralı; JSON dump | implemented (layered, tam savunma değil) |
| Model yanıtından bağımsız tool/veri kısıtı | Claude'a tool bağlı değil; DQL katalogdan; router çıktısı yalnız niyet/pencere/uygulama (uygulamalar `cfg["apps"]` ile filtrelenir) | implemented (yapısal) |
| Güvenilmeyen verinin politika/connector/model ayarını değiştirmesi | Ayarlar yalnız admin API ile; LLM çıktısı ayar yazmaz | implemented |
| Yapılandırılmış çıktı doğrulaması | `with_structured_output(..., method="json_schema")` + pydantic (`ChatRoute`, `AnalysisRequest`); ayrıştırılamazsa `LlmUnavailable` → deterministik yedek | partial (`extra="forbid"` yok; enum/literal alanlar sınırlı) |
| Güvenli render | `components/Markdown.tsx`: react-markdown, ham HTML kapalı, `rel="noopener noreferrer"` | implemented |
| Üretilen kodun çalıştırılmaması | Kod/komut çalıştıran yol yok | implemented |
| Injection tespiti | Yok | missing |
| Hipotez vs doğrulanmış bulgu | Prompt kuralları + evidence ID'leri; deterministik puanlayıcı | partial (prompt'a dayalı) |

**Risk:** Telemetri (log satırı, operasyon adı) içindeki metin LLM açıklamasını yanıltabilir; tool/secret erişimi
yapısal olarak olmadığı için etki "yanlış açıklama" ile sınırlı. **Önerilen:** sentetik injection fixture'larıyla
regresyon testi (T06), `extra="forbid"`, şüpheli kalıpların raporda işaretlenmesi. **Test:** T06, T10.

### AGT-03 Execution limits ve dayanıklılık — partial

| Limit | Kanıt | Durum |
|---|---|---|
| Recursion | `RunConfig` → `recursion_limit` (Settings) | implemented |
| Wall-clock | `run_timeout_s` (Settings), worker lease = timeout + 120 | implemented |
| Ek iterasyon | `max_extra_iterations` | implemented |
| Tool çağrı sayısı | `McpGateway.max_queries` (`max_queries_per_run`) | implemented |
| Query timeout/retry | `query_timeout_s`, `query_retries`, üstel backoff (`min(2**attempt, 8)`) | partial (jitter yok, circuit breaker yok) |
| Eşzamanlılık | `rt.sem` (`max_concurrent_runs`), `MAX_RUNS_PER_USER=2` (`api/routes/agent.py: _check_limits`) | implemented |
| LLM token | `max_tokens` profil/bütçe başına | partial (per-run toplam token tavanı yok) |
| Maliyet | Aylık profil bütçesi (`llm_profiles.month_cost`) | partial (LangSmith senkronuna bağlı, F15) |
| Sorgu yanıt boyutu | Kayıt sayısı/bayt sınırı yok | missing |
| Kill switch | `agent.enabled=false` → yeni run yok (`RunNotAllowed`) | partial (aktif run'ları iptal etmez; resume atlıyor F4) |
| İptal | `run.cancel` event; `POST /agent/runs/{id}/cancel` | implemented (kooperatif) |
| Resume'da bütçe | Resume yalnız kalan node'ları çalıştırır; analiz tekrarlanmaz (`test_interrupted_turn_resumes_from_checkpoint`) | partial (bütçe sayaçları kalıcı değil) |
| Deterministik skor bağımsızlığı | `airlab_agent/scoring.py`; LLM skor üretmez | implemented |
| Kısmi rapor, sahte veri yok | `McpUnavailable` → PARTIAL, `data_banner`; `test_mcp_down_is_reported_not_faked` | implemented |
| Limit aşımında audit | Yok | missing |

### AGT-04 Human-in-the-loop — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Read-only varsayılan | Yazma yapan tool yok; connector politikası yazma/destructive tool onayını reddeder (`connectors.py`, ipuçlarına dayalı) | implemented (yapısal) |
| Onay isteği kalıcı, süreli | `approval_requests`; `repositories/approvals.py: request/expire_due/decide` (`expires_at`, PENDING kontrolü, `FOR UPDATE`) | implemented |
| Bağımsız onaylayıcı | `decide`: `requested_by == decided_by` → Conflict; karar yalnız `admin` | implemented |
| Argüman/policy sürümü bağlama, yürütmede yeniden yetki | Onaylanan isteği yürüten bir yol yok | not applicable (bugün) — yürütme eklenirse zorunlu |
| Agent'ın kendi kendine onayı | Agent onay API'sini çağırmaz | implemented |

### MCP-01 Trusted connector registry — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Yalnız yetkili admin kayıt/onay | `POST/PATCH/DELETE /settings/connectors` → `admin`; audit kaydı | implemented |
| Onaylı endpoint, transport, auth tipi, tool listesi | `Connector` modeli (`endpoint`, `transport`=streamable_http, `secret_ref`, `allowed_tools`); `ConnectorIn` doğrulaması | implemented |
| Test izni ≠ kullanım/yönetim | Test/discover `admin`; kullanım (run) `sre` | partial (permission ayrımı yok; aynı admin rolü) |
| Tool tanımı sabitleme / değişiklik tespiti | `capabilities.tools` keşif anında saklanır; hash/diff yok | missing |
| Bilinmeyen tool reddi | `allowed_tools` dışı tool onaylanamaz; gateway yalnız `execute-dql` çağırır | implemented |
| Argüman semantiği doğrulaması | DQL katalogdan üretilir; çağrı anında doğrulama yok | partial |
| Least-privilege provider token | Dynatrace token kapsamı kodda doğrulanmıyor | missing (operasyonel doğrulama gerekli) |

### MCP-02 Network ve protokol — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| TLS doğrulama | httpx varsayılanı (verify açık); kapatan kod yok | implemented (varsayılan) |
| Kurumsal CA / mTLS | Yapılandırma yok | missing |
| SSRF: private/loopback/link-local/metadata reddi | `netguard.check_url` (tüm DNS adresleri), `SRE_OUTBOUND_ALLOWLIST`; `test_settings.py` netguard testleri | implemented (kayıt/test anında) |
| Çalışma anında kontrol / DNS rebinding | `gateway_factory`, `slack_notify`, LLM istemcisi çağrı öncesi kontrol etmiyor | missing (F7) |
| Redirect | MCP SDK yalnız aynı origin içi redirect izler (`mcp/shared/_httpx_utils.py: stream_within_origin`) | implemented (kütüphane) |
| Ağ seviyesinde egress | `airlab-agent` ağı internete açık; host firewall kanıtı yok | missing (altyapı bağımlılığı) |
| stdio MCP | Desteklenmiyor (yalnız Streamable HTTP; arayüzde komut alanı yok) | implemented |
| Credential yönlendirmesi | Token yalnız ilgili connector URL'sine `Authorization` başlığıyla | implemented |
| Timeout, retry, eşzamanlılık | `timeout_s`, `retries`, `concurrency=4` | implemented |
| Payload limiti | Yanıt boyutu sınırı yok | missing |

### MCP-03 Dynatrace/DQL — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Merkezî sorgu arayüzü | Tüm DQL'ler `McpGateway.run_many` üzerinden | implemented |
| Güvenilir şablonlar | `airlab_agent/signals.py` sinyal kataloğu; kullanıcı DQL yazamaz; LLM DQL üretmez | implemented |
| Zaman aralığı / limit doğrulaması | `resolve_window`: `max_minutes`, ingest lag | partial (çağrı anında dataset/limit doğrulaması yok) |
| Çıktı boyutu | Yok | missing |
| Provider tarafı scope | Kodda doğrulanamaz | missing (operasyonel) |
| Evidence ID ve durum | `QueryResult.evidence_id`, `query_id`, `status`; `investigation_evidence`, `agent_tool_executions` | implemented |
| Downstream öncesi sanitizasyon | `strip_fields` (yalnız chaos etiketleri), `redact` (regex) | partial |

### LLM-01 Provider management — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Onaylı provider/model allowlist | `settings/schemas.py` `LLM_PROVIDERS`: yalnız Anthropic oluşturulabilir; model listesi API'den | partial (model allowlist yok; herhangi bir Claude modeli seçilebilir) |
| Dış LLM'i kapatma | `agent.llm_enabled`, profil `enabled`; `RunConfig.llm_chat/llm_rca` | implemented (T08 için açık "dış istek sayısı 0" testi yok) |
| Sessiz fallback yok | Başka sağlayıcıya geçiş kodu yok; yalnız provider tarafı refusal fallback | implemented |
| TLS, timeout | `default_request_timeout`, `max_retries` | implemented |
| Token/maliyet sınırı | `max_tokens`; aylık bütçe (F15) | partial |
| Kayıt (model, request id, usage, latency) | LangSmith + `sre.llm_runs` senkronu | partial (request ID ve prompt/policy sürümü yok; tracing kapalıysa yok) |
| Sağlayıcı saklama/eğitim şartları | Dokümante değil | missing (idari) |

### DLP-01 Veri minimizasyonu ve redaction — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Sınıflandırma | Yok | missing |
| Secret maskeleme | `redact.py` kalıpları (Dynatrace, Anthropic, LangSmith, `password=` vb.) + `register_secrets` (gerçek env değerleri birebir) | implemented (regex + bilinen değer) |
| Kişisel veri tespiti | Yok | missing |
| LLM'e minimum veri | `prompts.analysis_payload`: toplulaştırılmış rapor alanları; ham kayıtlar gönderilmez | implemented |
| Tracing öncesi redaction | `airlab_agent/tracing.py`: `Client(hide_inputs=_hide, hide_outputs=_hide)` → `redact` | implemented (regex kapsamıyla sınırlı) |
| Slack / log / export | Slack metni redakte; loglar `redact_text` (hata mesajlarında); RCA markdown export org genelinde | partial |
| Pseudonymization | Yok | missing |
| Retention | `retention` işi: investigation, watch, llm_runs, job_runs; Store/checkpoint otomatik silinmez | partial |

### SEC-01 Secret management — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Envanter | `.env.example`, `docs/settings.md` | partial (tek belge halinde değil) |
| Authenticated encryption, anahtar DB dışında | `settings/secrets.py`: Fernet, anahtar `SRE_SETTINGS_KEY` env'de | implemented (kod) / **VM'de devre dışı** (F14) |
| API'de düz metin yok | `GET /settings/secrets` yalnız metadata; `test_settings.py` (T15) | implemented |
| env referansı allowlist | `secrets.check_ref`, `SRE_SECRET_ENV_ALLOWLIST` | implemented |
| Rotasyon/expiry/iptal | `rotated_at` alanı; expiry yok | partial |
| Vault/KMS | Yok | missing (P2) |
| `.env` commit edilmemesi | `.gitignore`: `.env`, `app-vm/*.env`, `deploy/out/`; `git grep` ile depoda gerçek token bulunmadı (yalnız sentetik test fixture'ları, F18) | implemented (araç taraması yapılmadı) |

### SEC-02 Encryption ve retention — missing

UI TLS yok (F1); PostgreSQL bağlantısında `sslmode` yok (internal Docker ağı); volume/backup şifrelemesi kanıtı yok;
anahtar yaşam döngüsü dokümante değil. Bilerek "kısmi" denmedi: internal ağ izolasyonu şifreleme değildir.

### MEM-01 State, checkpoint ve memory — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Thread sahipliği | `conversations.get(s, id, u.id)` (mesajlar, resume, working memory); thread_id istemciden kimlik olarak kabul edilmez | implemented; `test_working_memory_api_is_owner_scoped` |
| Checkpoint okuma/silme | Silme `admin` + onay metni (`settings: delete_thread`); thread listesi yalnız sayısal | implemented |
| Store namespace'leri | `MemoryService.last_investigation` kullanıcının/konuşmanın namespace'i; `app` namespace'i org geneli | partial (org sınırıyla tutarlı) |
| Episodic memory | Org geneli; LLM çıktısından türetilmez | partial (F5 kararına bağlı) |
| DB seviyesinde ayrım | Yok (F6) | missing |
| Güvenli serializer | LangGraph varsayılanı (`JsonPlusSerializer`); pickle fallback etkinleştiren kod yok | partial (açıkça sabitlenmemiş) |
| Resume'da politika/bütçe | Rol ve sahiplik yeniden kontrol; `check_allowed` yok (F4) | partial |
| Retention / silme | Checkpoint ve Store otomatik silinmez; episode investigation ile silinir | partial |
| Prosedürel bellek (runbook) düzenleme | `POST /runbooks` `admin`; sürüm geçmişi yok | partial |

### AUD-01 Audit events — partial

| Olay | Kanıt | Durum |
|---|---|---|
| Ayar değişikliği, rollback, connector/LLM/secret yaşam döngüsü, test/discover, run-now, checkpoint silme | `settings/service.audit` (16 çağrı, `api/routes/settings.py`), redakte before/after | implemented |
| Reddedilen ayar değişikliği | `_audit_separate` (ayrı transaction) | implemented |
| Login başarı/başarısızlık | Yok | missing |
| Kullanıcı/rol değişikliği | `/users` uçlarında yok | missing |
| Run başlatma/iptal/resume | Yalnız uygulama logu (`sre.api http`) | missing |
| Approval kararları | Tablo durumu var; audit olayı yok | partial |
| Memory erişimi/silme (focus temizleme) | Yok | missing |
| Export (RCA markdown) | Yok | missing |
| Şema (session_id, source_ip, correlation_id, run_id, reason_code, policy_version) | `AuditEvent`: actor, action, section, target, revision, before/after, result, detail | partial |
| Append-only | Uygulama yalnız INSERT ediyor; DB rolü UPDATE/DELETE yapabilir | partial (uygulama seviyesi; T14 başarısız olur) |
| Filtre, sayfalama, UTC | `GET /settings/audit` (`sre`), bölüm filtresi, sayfalama | implemented |
| Export (formül injection korumalı) | Yok | missing |
| Audit yazılamazsa fail-safe | Audit aynı transaction'da (Settings) → yazılamazsa işlem geri alınır | partial (yalnız Settings için) |

### AUD-02 SIEM ve alerting — missing

Yapılandırılmış JSON uygulama logu var (`core/logging.py`), ancak güvenlik olayı akışı, Syslog/CEF/webhook çıkışı,
teslim izleme ve alarm kuralı yok.

### API-01 — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Şema doğrulama, uzunluklar | Pydantic şemaları (`api/schemas.py`, `settings/schemas.py`: `extra="forbid"` Settings'te) | implemented |
| Parametreli SQL | SQLAlchemy ORM; ham `text()` yalnız sabit sorgularda (`settings: memory_status`) | implemented |
| BOLA/IDOR | Konuşma/run/memory sahiplik kontrolü; kayıtlar org geneli (F5) | partial |
| Mass assignment | Ayrı `*Patch` şemaları; `update_user` yalnız izinli alanlar | implemented |
| XSS | React + react-markdown (HTML kapalı), CSP `script-src 'self'` | implemented |
| CSRF | Bearer header (cookie yok) | not applicable (bugün) |
| CORS | Varsayılan kapalı; yalnız `SRE_CORS_ORIGINS` beyaz listesi, `allow_credentials=False` | implemented |
| Güvenlik başlıkları | nginx: CSP, `X-Frame-Options DENY`, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`; backend `nosniff`, `Referrer-Policy` | partial (HSTS yok — TLS yok) |
| Body boyutu | nginx `client_max_body_size 1m` | implemented |
| Rate limit | Login + Settings test uçları; genel yok | partial (F13) |
| Hata yanıtları | Yakalanmamış hata → genel mesaj + correlation ID; `ValueError` → `str(e)` (F12) | partial |
| API docs | `/api/docs` kimliksiz (F11) | missing (kısıtlama) |
| SSE yetkisi | `GET /agent/stream/{id}`: `current_user` + `_own_run`; fetch tabanlı, token URL'de değil (`services/sse.ts`) | implemented (bağlantı süresince yeniden doğrulama yok) |
| Export yetkisi | RCA markdown: `current_user` (org geneli) | partial |

### INF-01 Deployment hardening — partial

| Alt gereksinim | Kanıt | Durum |
|---|---|---|
| Non-root | backend/worker: `USER sre` (`backend/Dockerfile`); frontend: nginx root | partial |
| Privileged / docker.sock | Platform servislerinde yok | implemented |
| cap_drop, no-new-privileges, read_only | Platform servislerinde yok (yalnız netem sidecar'da örnek var) | missing |
| Kaynak limitleri | `mem_limit`, `cpus` tüm platform servislerinde | implemented |
| Healthcheck | backend, postgres, frontend var; worker `disable: true` | partial |
| Ağ ayrımı | `sre-data` internal (PG dışa kapalı, port yayımlanmaz); frontend yalnız `sre-web` | implemented |
| Yayımlanan port | `8090` tüm arayüzler; diğer lab servisleri `127.0.0.1` | partial |
| DB rolleri | Tek rol (F6) | missing |
| İmaj sabitleme | Tag ile (`python:3.13-slim`, `nginx:1.29-alpine`); digest yok | partial |
| Backup/restore, RPO/RTO | Yok | missing |
| Scheduler tekrarı | `jobs.claim` `FOR UPDATE SKIP LOCKED` + lease; run-now çift koşu 409 (`test_settings`) | implemented |
| Air-gap | Test edilmedi | not verified |

### UI-01 Security & Governance frontend — partial

Mevcut: Settings > **Security & Audit** (secret metadata, env referans durumu, rol tablosu, lock/allowlist görünümü,
audit log tablosu); MCP Servers (connector, tool onayı, test); LLM Providers (profil, bütçe, test);
Retention & Memory; Onaylar sayfası (`/approvals`). Hepsi gerçek API'lere bağlı; sahte metrik yok.
Eksik: ayrı Security & Governance modülü, oturum/kimlik ekranı, permission matrisi, network/SIEM/compliance
ekranları, approval ayrıntısında argüman bağlama. UI kontrolleri backend enforcement olmadan eklenmemeli.

### SDLC-01 — missing

Depoda CI tanımı yok; SAST, secret tarama, SCA, container tarama, SBOM, DAST, AI regresyon testi yok.
Kilit dosyaları: `sre-agent/requirements.lock`, `sre-platform/backend/requirements.lock`, `frontend/package-lock.json`
(pinning var — partial). `SECURITY.md` yok.

## 5. Test matrisi (T01–T22) — mevcut durum

| ID | Durum | Kanıt / açık |
|---|---|---|
| T01 | implemented | `test_api.py: test_health_auth_rbac` (401 + correlation_id) |
| T02 | partial | 403 test edili; audit olayı yok |
| T03 | partial | Konuşma/memory/run sahipliği test edildi; investigation/RCA org geneli (F5) |
| T04 | partial | Süresi dolmuş JWT reddedilir; devre dışı kullanıcı reddedilir; iptal edilmiş oturum kavramı yok |
| T05 | partial | Gateway yalnız `execute-dql`; onaysız tool → `McpUnavailable` (`test_settings`); argüman değişikliği kontrolü yok |
| T06 | missing | Injection fixture testi yok |
| T07 | partial | Redaction birim/entegrasyon testleri (`test_persistence`, `test_langsmith_trace`); PII yok |
| T08 | partial | Kontrol var; "dış istek yapılmadı" testi yok |
| T09 | partial | netguard testleri (private/loopback/metadata/http); rebinding fixture yok |
| T10 | partial | Ayrıştırılamayan çıktı → deterministik yedek (`test_llm_outage_falls_back_to_deterministic`); şema ihlali testi yok |
| T11 | partial | Limitler var; audit yok; token tavanı yok |
| T12 | partial | Rol DB'den okunur; resume `check_allowed`'ı atlıyor (F4) |
| T13 | partial | Süre, tekrar karar, dört göz (`approvals.decide`); argüman bağlama yok |
| T14 | missing | DB rolü audit kaydını güncelleyebilir/silebilir |
| T15 | implemented | `test_settings.py` secret maskeleme |
| T16 | missing | Policy motoru yok |
| T17 | partial | Scheduler lease ve run-now testi; resume'da Slack bildirimi tekrarı doğrulanmadı |
| T18 | implemented | 71 + 84 + 37 test geçti (2026-11-25) |
| T19 | partial | SSE sahiplik kontrolü kodda; export org geneli; service account yok |
| T20 | missing | Backup/restore yok; migration downgrade/upgrade testleri var (`test_settings`) |
| T21 | implemented | `test_settings.py`: RBAC, doğrulama, 409, audit |
| T22 | missing | SIEM yok |

## 6. Korunacak güçlü yönler

Bunlar plan boyunca **değiştirilmemeli**:

- ADR-007: LLM'e tool bağlanmaması, sabit DQL kataloğu, deterministik risk skoru.
- Kısmi rapor / sahte veri yok davranışı (`McpUnavailable` → PARTIAL).
- Settings modülündeki revizyon, optimistic concurrency, audit, secret referansı ve SSRF guard.
- Konuşma ve working memory sahiplik kontrolleri; checkpoint'ten sürdürme.
- Scheduler lease (`FOR UPDATE SKIP LOCKED`) ve run-now çift koşu koruması.

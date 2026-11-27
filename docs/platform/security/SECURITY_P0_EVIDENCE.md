# Security P0 — Implementation Evidence

Plan: [SECURITY_IMPLEMENTATION_PLAN.md](SECURITY_IMPLEMENTATION_PLAN.md) §3 · Gap analizi:
[SECURITY_GAP_ANALYSIS.md](SECURITY_GAP_ANALYSIS.md) · Tarih: 2026-11-25

**Durum:** P0 kodu, testleri ve yapılandırması tamamlandı, test edildi ve **2026-11-25 APP_VM'e deploy edildi**
(kullanıcı onayıyla; aktif chaos deneyi yokken). Canlı doğrulama §3.1.

## 1. Özet

| Plan | Gereksinim | Durum | Enforcement noktası |
|---|---|---|---|
| P0.1 TLS | IAM-01, SEC-02 | **Risk kabulü** (karar 2) → telafi kontrolleri uygulandı | §2.1 |
| P0.2 Oturum yaşam döngüsü | IAM-02 | Uygulandı | `api/deps.current_user` → `security/sessions.validate` |
| P0.3 Resume policy açığı | AGT-01/03, MEM-01 | Uygulandı | `api/routes/agent.resume` → `settings_runtime.check_allowed` |
| P0.4 Güvenlik audit'i | AUD-01 | Uygulandı | `security/audit.record/record_separate`; DB tetikleyicisi |
| P0.5 Çalışma anında SSRF | MCP-02 | Uygulandı | `connectors.gateway_factory`, `llm_profiles.task_profiles`, `slack_notify._post` |
| P0.6 Throttle / rate limit | IAM-01, API-01 | Uygulandı | `security/throttle`, `api/routes/auth.login`, `api/routes/agent._admit` |
| P0.7 Container / API yüzeyi | INF-01, API-01 | Uygulandı (deploy bekliyor) | `app-vm/docker-compose.yml`, `frontend/Dockerfile`, `api/app.py` |
| P0.8 Secret deposu | SEC-01 | Tamamlandı (VM'de etkin) | `SRE_SETTINGS_KEY`, `store_available: True` |
| P0.9 CI + secret tarama | SDLC-01 | Uygulandı (ilk koşu push bekliyor) | `.github/workflows/ci.yml`, `.gitleaks.toml`, `SECURITY.md` |

## 2. Gereksinim bazında kanıt

### 2.1 P0.1 — TLS yok (risk kabulü) ve telafi kontrolleri

Kullanıcı kararı (plan §11, karar 2). Parola ve bearer token ağda okunabilir; aşağıdakiler riski **azaltır, kaldırmaz**:

| Telafi kontrolü | Kanıt |
|---|---|
| Çalınan token'ın ömrü sınırlı: idle 30 dk, sunucu tarafı iptal | §2.2 |
| Kalıcı login throttle + başarısız giriş audit'i | §2.6, §2.4 |
| UI yayın arayüzü daraltılabilir | compose `"${SRE_UI_BIND_IP:-0.0.0.0}:${SRE_UI_PORT:-8090}:8080"` |
| CSP / X-Frame-Options / nosniff korunuyor | izole container testinde başlıklar doğrulandı (§3) |

Varsayılan bind `0.0.0.0` kaldı (mevcut erişim bozulmasın). **Öneri:** `SRE_UI_BIND_IP`'yi yönetim ağı arayüzüne
ayarlayın ve host firewall ile 8090'ı yalnız yönetim ağına açın.

### 2.2 P0.2 — Sunucu tarafı oturumlar (IAM-02, T04)

| Kontrol | Kod |
|---|---|
| Her token `jti` = `sre.auth_sessions.id`; `jti` zorunlu claim, algoritma sabit HS256 | `core/security.create_token/decode_token` |
| Her istekte: imza, süre, kullanıcı aktif, oturum var / iptal değil / mutlak süre / idle | `api/deps.current_user`, `security/sessions.validate` (fail-closed) |
| `POST /auth/logout` (sunucu tarafı iptal), `GET /auth/sessions`, `DELETE /auth/sessions/{id}` (sahip/admin; başkasına 404) | `api/routes/auth.py` |
| `POST /auth/password` (mevcut parola zorunlu; diğer oturumlar iptal) | `auth.change_password` |
| Admin rol değişikliği / devre dışı / parola sıfırlama → kullanıcının tüm oturumları iptal | `auth.update_user` → `sessions.revoke_user` |
| SSE akışı her keepalive'da oturumu yeniden doğrular; iptalde `session_revoked` olayı + kapanış | `api/routes/agent.stream`, frontend `services/sse.ts` |
| Frontend çıkışı sunucu oturumunu iptal eder | `frontend/src/services/auth.tsx: logout` |
| Süresi 30+ gün önce dolmuş oturumlar saatlik retention işinde silinir | `worker.retention` → `sessions.purge_expired` |
| Migration | `0005_auth_sessions` (up/down test edildi) |

### 2.3 P0.3 — Resume policy açığı (T12)

`POST /conversations/{id}/resume` artık chat ile aynı `check_allowed(..., "manual")` kapısından ve hız sınırından
geçer. Reddedilen istek `run.denied` / `policy_agent_settings` olarak audit'e yazılır (`api/app._rn`).

### 2.4 P0.4 — Güvenlik audit'i (AUD-01, T02, T14)

| Olay (action) | Sonuç değerleri | Yazıldığı yer |
|---|---|---|
| `auth.login` | ok / denied (`bad_credentials`, `throttled`) | `auth.login` |
| `auth.logout`, `auth.session.revoke`, `auth.session.rejected` (`revoked:*`, `idle_timeout`, `expired`, `unknown_session`) | | `auth.*`, `deps.current_user` |
| `auth.password.change` | ok / denied (`bad_current_password`) | `auth.change_password` |
| `authz.denied` (`requires_<rol>`) — her 403 | denied | `deps.require` |
| `user.create`, `user.update`, `user.role_change`, `user.disable` (before/after diff) | ok | `auth.create_user/update_user` |
| `run.start` (run_id ile), `run.cancel`, `run.denied` (`policy_agent_settings`, `rate_limited`) | | `agent._started/_admit/cancel`, `app._rn`, `worker.health_check` |
| `approval.request`, `approval.decide` (denied: `four_eyes`, `invalid_state`), `approval.cancel` | | `records.py` |
| `memory.focus.clear`, `export.rca_markdown` | ok | `agent.clear_focus`, `records.rca_markdown` |
| `egress.denied` (`ssrf_guard`) | denied | §2.5 |
| Mevcut Settings olayları | | `settings/service.audit` → aynı modül |

- Her kayıtta `actor_type`, `source_ip`, `correlation_id`, `session_id`, `run_id`, `reason_code` (migration `0006`).
- before/after/detail redakte edilir; testte parolaların audit'e girmediği doğrulanır.
- Reddedilen işlemler ayrı transaction'da yazılır (`record_separate`), başarılı işlemler işlemle aynı transaction'da.
- **Append-only:** `BEFORE UPDATE OR DELETE` satır tetikleyicisi + `BEFORE TRUNCATE` deyim tetikleyicisi →
  `insufficient_privilege` hatası. Canlı veri kopyasında doğrulandı (§3).
- `GET /settings/audit` yeni alanları döner; `action` ve `result` filtresi eklendi.

### 2.5 P0.5 — Çalışma anında SSRF kontrolü (MCP-02, T09)

`netguard.check_runtime(url)` (kayıt anındaki kuralların aynısı: yalnız https, kullanıcı bilgisi yok, tüm DNS
adresleri global) **her koşu / her gönderim öncesi** çağrılır:

- MCP: `connectors.gateway_factory` → engellenirse `McpUnavailable` (kısmi rapor, sahte veri yok) + audit.
- LLM: `llm_profiles.task_profiles` (özel `base_url`) → o görevde LLM yok (deterministik yedek) + audit.
- Slack: `slack_notify._post` (her gönderim, `follow_redirects=False`) → bildirim gönderilmez + audit.

### 2.6 P0.6 — Kalıcı throttle ve hız sınırı

- Login: `sre.auth_login_failures` (anahtar `kullanıcı adı | istemci IP`), 5 deneme / 300 sn → 429 + `Retry-After`;
  backend restart'ı sıfırlamaz (testle doğrulandı).
- İstemci IP'si: X-Forwarded-For yalnız `SRE_TRUSTED_PROXIES` içindeki eşten gelirse ve yalnız **en sağdaki**
  değer (nginx'in eklediği) kullanılır (`security/context.resolve_client_ip`). Compose varsayılanı `172.16.0.0/12`
  (backend portu yayımlanmaz; eşler yalnız platform container'ları).
- Agent çalıştırma: kullanıcı başına dakikada `SRE_RUN_RATE_PER_MINUTE` (6) → 429; sayaç `run.start` audit
  olaylarından (kalıcı). Mevcut eşzamanlılık sınırı (`MAX_RUNS_PER_USER=2`) korunur.

### 2.7 P0.7 — Container sertleştirme ve API yüzeyi

| Servis | Değişiklik |
|---|---|
| sre-frontend | root olmayan nginx (uid 101), container portu 8080, `cap_drop: ALL`, `no-new-privileges`, `read_only` + tmpfs |
| sre-backend, sre-worker, sre-migrate | `cap_drop: ALL`, `no-new-privileges`, `read_only` + tmpfs (`/tmp`, `/home/sre`); zaten `USER sre` |
| sre-worker | heartbeat healthcheck (`/tmp/sre-worker.heartbeat`, eşik 900 sn) |
| sre-postgres | `no-new-privileges` (entrypoint root→postgres geçişi için cap_drop uygulanmadı) |
| API | `/api/docs`, `/api/openapi.json` varsayılan kapalı (`SRE_API_DOCS`) |

### 2.8 P0.8 — Secret deposu

2026-11-25: VM'de `SRE_SETTINGS_KEY` üretildi (değer gösterilmedi; env yedeği alındı), `store_available: True`.
Secret girişi ve rotasyon kullanıcı tarafından platformdan yapılacak (karar 5).

### 2.9 P0.9 — CI

`.github/workflows/ci.yml`: backend+agent testleri (PostgreSQL 17 service), frontend tsc/vitest/build, gitleaks
(tüm geçmiş, `--redact`), pip-audit (`--strict`), npm audit (`--audit-level=high`). `permissions: contents: read`,
`persist-credentials: false`. `.gitleaks.toml` yalnız sentetik olduğu doğrulanan 5 sabit değeri muaf tutar.
`SECURITY.md` bildirim politikası.

## 3. Test sonuçları (gerçek çıktılar, 2026-11-25)

| Paket | Ortam | Sonuç |
|---|---|---|
| `tests/test_security_p0.py` (yeni, 13 test) | APP_VM, `airlab/sre-platform-backend` imajı, gerçek PostgreSQL 17 | **13 passed** |
| Backend tamamı (`sre-platform/backend/tests`) | aynı | **84 passed, 1 skipped** (önce 71 + 1 skipped; ilk koşuda `test_models_match_migration` yeni indekslerin modelde eksik olduğunu yakaladı → düzeltildi) |
| sre-agent (`sre-agent/tests`) | aynı imaj, `--network none` | **84 passed** |
| Frontend | `node:22-alpine` | tsc OK, **38 passed** (yeni: `session_revoked` testi), build OK |
| Migration provası | Canlı DB'nin `pg_dump` kopyası (`sre_p0rehearsal`), yeni imaj, `read_only` + `cap_drop ALL` | 0004 → 0006 OK; `DELETE FROM sre.audit_events` → `append-only (DELETE denied)`; downgrade 0004 OK, satırlar korundu; kopya silindi |
| Sertleştirilmiş container'lar (izole, canlı servislere dokunmadan) | `p0test` tag'li imajlar | frontend uid 101, `/healthz` ok, `/api/health` proxy ok, CSP/X-Frame-Options başlıkları var; backend uid 999, read-only kökte import + heartbeat ok |
| Compose | `docker compose --profile platform config` | geçerli; etkin ayarlar doğrulandı |
| CI workflow | — | YAML/TOML sözdizimi doğrulandı; **GitHub'da henüz koşmadı** (push onayı gerekiyor) |
| gitleaks / pip-audit / npm audit | — | **Çalıştırılmadı** (yerelde kurulu değil; ilk CI koşusunda çalışacak) |

Yeni testlerin kapsadığı negatif senaryolar: logout sonrası token, sunucuda oturumu olmayan doğru imzalı token,
`jti`'siz token, `alg=none`, idle/mutlak süre aşımı, başkasının oturumunu iptal (404), yanlış mevcut parola,
rol değişikliği/devre dışı/parola sıfırlama sonrası eski oturum, restart sonrası throttle, sahte X-Forwarded-For,
hız sınırı, agent devre dışıyken resume ve chat, viewer'ın admin ucu (audit'li 403), audit UPDATE/DELETE/TRUNCATE,
dört göz ihlali, DNS rebinding (MCP + LLM), metadata adresine Slack gönderimi, `/api/docs` erişimi,
audit'te parola sızıntısı.

### 3.1 Canlı deploy doğrulaması (APP_VM, 2026-11-25)

| Kontrol | Sonuç |
|---|---|
| Migration | `0004 → 0006` (`migration tamam`, revision 0006) |
| Container durumu | sre-frontend/backend/worker/postgres `healthy` |
| Çalışma kullanıcısı / kök FS / yetenekler | frontend `nginx`, backend/worker `sre`; `ReadonlyRootfs=true`, `CapDrop=[ALL]`, `no-new-privileges:true` |
| `GET /healthz`, `/api/health`, `/` | 200 |
| `GET /api/openapi.json`, `/api/docs` | 404 |
| `GET /api/auth/me` (token yok) | 401 |
| Başarısız login | 401 + audit `auth.login / denied / bad_credentials`, `source_ip` = gerçek istemci (nginx değil) |
| Güvenlik başlıkları | CSP, `X-Frame-Options: DENY` |
| Worker | `watch` ve `langsmith-sync` işleri `ok` (çalışma anı SSRF kontrolü MCP'yi engellemedi); heartbeat dosyası güncel; log'da hata yok |
| Mevcut oturumlar | Deploy anında `auth_sessions` boş → tüm eski token'lar geçersiz (beklenen) |

Kullanıcı tarafından doğrulanacak: arayüzde yeniden giriş, çıkış sonrası eski sekmenin 401 alması, bir sohbet turu.

## 4. Kalan riskler ve sınırlamalar

| # | Risk | Neden kaldı | Plan |
|---|---|---|---|
| R1 | Ağda açık metin parola/token | TLS kararı (risk kabulü) | Telafi §2.1; TLS kararı değişirse P0.1 hazır |
| R2 | Token `localStorage`'da (XSS halinde çalınabilir) | TLS olmadan `Secure` cookie anlamsız | CSP `script-src 'self'` korunuyor; P1.4 ertelendi |
| R3 | Tablo sahibi DB rolü audit tetikleyicilerini kaldırabilir | Tek DB rolü | P1.5 DB rol ayrımı |
| R4 | DNS kontrolü ile bağlantı arasında adres değişebilir (TOCTOU) | Kontrol bağlantıdan önce, ayrı DNS çözümü | P2.6 ağ seviyesinde egress kısıtı |
| R5 | Açık sekmedeki periyodik sorgular oturumu "aktif" tutar (idle süresi yalnız sekme kapalıyken işler) | İstek bazlı idle ölçümü | Kabul; gerekirse kullanıcı etkileşimine bağlı idle (P2.8) |
| R6 | SSE oturum yeniden doğrulaması yalnız keepalive'da (15 sn olaysız geçince); sürekli olay akarken iptal edilen oturum o çalıştırmanın akışını bitene kadar izleyebilir | Olay başına DB sorgusu maliyeti | Kabul; çalıştırma süresi `run_timeout_s` ile sınırlı |
| R7 | Zamanlanmış koşuların başlangıcı audit'e yazılmaz (yalnız reddi); `job_runs` tablosunda kayıt var | Her 15 dk gürültü | P1.1 policy karar logu |
| R8 | Oturum listesi / parola değişikliği için arayüz ekranı yok (API var) | P0 kapsamı backend | P2.8 Security & Governance UI |
| R9 | CI ve tarayıcılar henüz koşmadı; gitleaks imaj sürümü ve action'lar tag ile sabit (SHA değil) | Push onayı yok | İlk koşu sonrası doğrulanacak; SHA pinleme P3 |
| R10 | Worker healthcheck eşiği 900 sn (uzun iş sırasında tur sürer) | Tur içi iş | Kabul |
| R11 | `SRE_TRUSTED_PROXIES` varsayılanı tüm Docker özel aralığı (172.16.0.0/12) | Ağ alt ağı otomatik atanıyor | Gerekirse sre-web alt ağı sabitlenip daraltılır |

## 5. Deploy ve rollback

**Uyumsuz etkiler:** (1) tüm mevcut token'lar geçersiz → herkes yeniden giriş yapar; (2) frontend container portu
8080 (host portu 8090 değişmez); (3) `/api/docs` kapanır.

```bash
# önkoşul: curl -s 172.20.229.26:8080/api/v2/experiments → active: null
cd /opt/airlab/app-vm
docker compose --profile platform build sre-migrate sre-frontend
docker compose --profile platform run --rm sre-migrate          # 0004 → 0006
docker compose --profile platform up -d sre-backend sre-worker sre-frontend
```

**Rollback:** `docker compose --profile platform run --rm sre-migrate migrate --downgrade 0004`, önceki kaynak /
compose ile yeniden build + `up -d`. Audit satırları korunur (yeni kolonlar kaybolur); oturum tabloları silinir.

# Security Implementation Plan

Girdi: [`SECURITY_GAP_ANALYSIS.md`](SECURITY_GAP_ANALYSIS.md) (bulgular F1–F18) ·
Gereksinim: [`SECURITY_IMPLEMENTATION.md`](SECURITY_IMPLEMENTATION.md) · Tarih: 2026-11-25

## 0. İlkeler

- **Korunacaklar:** ADR-007 (LLM'e tool yok, sabit DQL kataloğu), deterministik risk skoru, kısmi rapor davranışı,
  Settings modülünün revizyon/audit/secret modeli, mevcut frontend tasarımı. Hiçbir adım bunları yeniden yazmaz.
- Her değişiklik **küçük ve geri alınabilir**: yeni davranış mümkün olduğunda bir ayar/feature flag arkasında gelir,
  varsayılanı güvenli olandır, migration'ların downgrade'i vardır ve test edilir.
- Her kontrol önce **backend enforcement + negatif test** olarak eklenir; UI ondan sonra gelir.
- Spec 2.7: production deploy, yıkıcı test, push, canlı credential rotasyonu ve dış servis değişikliği ancak açık
  onayla. Testler yalnız yerel/staging fixture'larıyla.
- Her fazın sonunda: backend + sre-agent + frontend testleri, `graphify update .`, `docs/` güncellemesi.

## 1. Uyumsuz (incompatible) değişiklikler — uygulamadan önce karar gerekir

| # | Değişiklik | Kimi etkiler | Önerilen geçiş |
|---|---|---|---|
| X1 | TLS zorunlu (HTTP → HTTPS, HSTS) | Tarayıcı yer imleri, `SRE_PUBLIC_URL`, Slack mesajlarındaki linkler | 80 → 443 redirect; HSTS önce kısa `max-age` (300), doğrulandıktan sonra uzun |
| X2 | Token `localStorage` → HttpOnly cookie (refresh) + kısa access token | Frontend `services/api.ts`, `sse.ts`; API istemcileri | Bir sürüm boyunca iki yol birden; cookie yolu flag arkasında |
| X3 | Sunucu tarafı oturumlar | Mevcut tüm JWT'ler (deploy anında herkes yeniden giriş yapar) | Bakım penceresinde; duyuru |
| X4 | Sohbet kaynaklı investigation görünürlüğü (F5) | Viewer/diğer SRE'lerin listesi; episode araması | **Karar gerekli:** (a) org genelinde paylaşım bilinçli kabul edilip dokümante edilir, ya da (b) `visibility` alanı (`org`/`owner`) eklenir, sohbet kaynaklılar `owner` olur |
| X5 | DB rol ayrımı (owner/migrate, app, worker, readonly) | `sre-platform.env`, compose, migrate akışı | Yeni roller eklenir, uygulama rolüne geçiş flag ile; eski tek rol rollback için kalır |
| X6 | Frontend container non-root (nginx 8080) | Compose port eşlemesi (`8090:80` → `8090:8080`) | Tek sürümde, rollback = önceki imaj |
| X7 | `/api/docs` kapanması | Geliştirici erişimi | `SRE_API_DOCS=false` varsayılan; geliştirmede açılabilir |
| X8 | Permission modeli | Mevcut roller birebir eşlenir; yetki daralması yok | Rol → permission tablosu; davranış değişmeden önce paralel çalıştırılıp karşılaştırılır |

## 2. Faz özeti

| Faz | Amaç | Bulgular | Tahmini iş |
|---|---|---|---|
| **P0** | Taşıma güvenliği, oturum iptali, policy boşluğu, temel audit, container sertleştirme, temel CI | F1, F2, F3, F4, F6 (kısmi), F7, F9 (temel), F10, F11, F13, F14, F18 | 6–8 gün |
| **P1** | Merkezi policy, LLM/MCP sertleştirme, DLP, audit tamamlama, API hataları | F8, F12, F15, F16, F17, F5 kararı, F6 (tam) | 8–10 gün |
| **P2** | Kurumsal kimlik (OIDC/MFA), service account, SIEM, Vault/KMS, backup, Security & Governance UI | IAM-01/03, AUD-02, SEC-01/02, INF-01, UI-01 | 10–15 gün |
| **P3** | Olgunluk: SBOM/provenance, DAST, injection eval seti, mTLS, air-gap, compliance raporu | SDLC-01, MCP-02, AGT-02 | süreklilik |

## 3. P0 — kritik açıklar

> **Durum (2026-11-25):** P0.2–P0.9 uygulandı ve test edildi; P0.1 risk kabulü + telafi kontrolleri. Kanıt, gerçek
> test sonuçları, kalan riskler ve deploy/rollback: [SECURITY_P0_EVIDENCE.md](SECURITY_P0_EVIDENCE.md).
> Plandan sapmalar: oturum ve throttle tabloları tek migration'da (`0005`); audit genişletmesi `0006` (TRUNCATE
> tetikleyicisi de eklendi); çalıştırma hız sınırı ek tablo yerine `run.start` audit olaylarından sayılır;
> P0.5'te önbellek kullanılmadı (her koşu öncesi DNS kontrolü). APP_VM deploy'u onay bekliyor.

### P0.1 TLS termination (F1) — IAM-01, SEC-02, API-01

> **Ertelendi (karar 2, §11):** TLS uygulanmayacak; yerine §11.1 telafi edici kontroller. Aşağıdaki tasarım ileride
> TLS kararı değişirse kullanılmak üzere korunur.

- **Değişiklik:** `frontend/nginx.conf`'a `listen 443 ssl` (sertifika volume'dan, `ssl_protocols TLSv1.2 TLSv1.3`),
  80 → 443 redirect, `Strict-Transport-Security` başlığı. Lab için kurumsal CA veya self-signed; sertifika repoya
  girmez. Compose: port `"${SRE_UI_BIND_IP:-0.0.0.0}:8443:443"`; bind IP'si yapılandırılabilir.
- **Bağımlılık:** sertifika kaynağı (kullanıcı kararı).
- **Migration:** yok. **Rollback:** önceki `nginx.conf` + imaj; HSTS kısa max-age ile başladığı için tarayıcı kilitlenmez.
- **Kabul:** `curl http://…:8090` → 301; HTTPS'de HSTS başlığı; `testssl`/`openssl s_client` ile TLS1.0/1.1 reddi;
  frontend E2E giriş HTTPS üzerinden.

### P0.2 Oturum yaşam döngüsü (F2) — IAM-02

- **Değişiklik:**
  1. Migration `0005_auth_sessions`: `sre.auth_sessions (jti PK, user_id FK, created_at, last_seen_at, expires_at,
     revoked_at, revoked_reason, ip, user_agent)`.
  2. `core/security.create_token` → oturum satırı; `api/deps.current_user` → `jti` aktif mi, idle süresi
     (`SRE_SESSION_IDLE_MINUTES`, varsayılan 30) aşılmış mı kontrol; `last_seen_at` en fazla dakikada bir güncellenir.
  3. `POST /auth/logout`, `GET /auth/sessions`, `DELETE /auth/sessions/{jti}` (sahip veya admin).
  4. Parola değişikliği, kullanıcı devre dışı bırakma, rol değişikliği → kullanıcının tüm oturumları iptal.
  5. Self-service parola değişikliği `POST /auth/password` (eski parola zorunlu).
  6. TTL: access token 8 sa → mutlak 8 sa kalır, idle 30 dk eklenir (X3).
  7. HttpOnly cookie geçişi (X2) **P1'e ertelenir**: önce sunucu tarafı iptal, sonra saklama yeri.
- **Migration:** `0005` (up: tablo; down: tablo drop). Eski token'lar `jti` tabloda olmadığı için reddedilir → herkes
  yeniden giriş yapar (X3).
- **Rollback:** `migrate --downgrade 0004` + önceki imaj.
- **Kabul (T04):** logout sonrası aynı token 401; parola değişimi sonrası eski token 401; 31 dk boşta kalan token 401;
  devre dışı kullanıcı 401; `GET /auth/sessions` yalnız kendi oturumları.

### P0.3 Resume yolunda policy kontrolü (F4) — AGT-01/03, MEM-01

- **Değişiklik:** `api/routes/agent.py: resume` → `settings_runtime.check_allowed(eff, "manual")` (chat ile aynı);
  `RunNotAllowed` → 409/403 (mevcut eşleme). Tek satırlık düzeltme; P1'deki policy katmanına taşınır.
- **Migration:** yok. **Rollback:** revert.
- **Kabul (T12):** `agent.enabled=false` iken resume → reddedilir, LLM/MCP çağrısı yok; etkinken mevcut
  `test_interrupted_turn_resumes_from_checkpoint` geçmeye devam eder.

### P0.4 Güvenlik audit'i (F3) — AUD-01

- **Değişiklik:**
  1. Migration `0006_audit_extend`: `audit_events`'e `actor_type` (user/system/service), `source_ip`,
     `correlation_id`, `session_id`, `run_id`, `reason_code` (nullable — geriye uyumlu).
  2. `settings/service.audit` → `security/audit.py`'ye taşınır (aynı imza + yeni alanlar; Settings çağrıları değişmez).
  3. Yeni olaylar: `auth.login.ok/failed/throttled`, `auth.logout`, `auth.session.revoked`, `user.create/update/
     role_change/disable`, `password.change`, `run.start/cancel/resume/denied`, `approval.request/decide/cancel`,
     `memory.focus.clear`, `export.rca_markdown`. Başarısız login ayrı transaction'da yazılır.
  4. **Append-only:** DB tetikleyicisi `BEFORE UPDATE OR DELETE ON sre.audit_events → RAISE EXCEPTION`; retention
     silmesi ayrı, yalnız owner rolünün çalıştırabildiği bir fonksiyonla (P1'de DB rol ayrımıyla tamamlanır).
- **Migration:** `0006` (down: kolonları ve trigger'ı kaldırır; satırlar korunur).
- **Rollback:** downgrade; yeni olay türleri eski UI'da da listelenir (section serbest metin).
- **Kabul (T02, T14):** 403 ve başarısız login audit'te; `UPDATE sre.audit_events` uygulama bağlantısıyla hata
  verir; olaylarda secret yok (redaction testi).

### P0.5 Çalışma anında SSRF kontrolü (F7) — MCP-02

- **Değişiklik:** `settings/connectors.gateway_factory` ve `services/slack_notify` çağrı öncesi
  `netguard.check_url` (sonuç kısa süreli önbellekte, ör. 60 sn); başarısızsa `McpUnavailable`/bildirim atlanır +
  audit `egress.denied`. LLM `base_url` için aynı kontrol `llm_profiles` çalışma yoluna.
- **Bağımlılık:** yok (netguard mevcut). Ağ seviyesinde egress kısıtı P2 (host firewall / proxy).
- **Kabul (T09):** DNS çözümü private adrese değiştirilen fixture'da çalışma anında çağrı reddedilir; meşru
  Dynatrace host'u etkilenmez.

### P0.6 Rate limit ve login throttle kalıcılığı (F13) — IAM-01, API-01

- **Değişiklik:** login throttle sayaçları PostgreSQL'e (`sre.auth_throttle`, migration `0005` içinde) veya
  `auth_sessions` ile aynı migration; gerçek istemci IP'si yalnız güvenilen proxy'den (`X-Forwarded-For`, nginx).
  Pahalı uçlara (`/chat`, `/agent/investigate`, `/conversations/*/resume`) kullanıcı başına dakika limiti (mevcut
  `MAX_RUNS_PER_USER` korunur).
- **Kabul:** 6. hatalı deneme 429, backend restart sonrası da 429; limit aşımı audit'te.

### P0.7 Container ve deployment sertleştirme (F10, F11, F6 kısmi) — INF-01, API-01

- **Değişiklik:**
  - `sre-backend`, `sre-worker`, `sre-migrate`: `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`,
    `read_only: true` + `tmpfs: /tmp`.
  - `sre-frontend`: `nginxinc/nginx-unprivileged` tabanı, port 8080 (X6), aynı sertleştirme.
  - `sre-postgres`: `no-new-privileges`.
  - Temel imajlar digest ile sabitlenir.
  - `api/app.py`: `/api/docs` ve `/api/openapi.json` `SRE_API_DOCS` ayarına bağlanır (varsayılan kapalı, X7).
  - Worker için basit healthcheck (son heartbeat zamanı dosyası).
- **Rollback:** önceki compose + imaj tag'i.
- **Kabul:** `docker inspect` ile `User` ≠ root, `CapDrop=ALL`, `ReadonlyRootfs=true`; `/api/docs` 404; tüm testler +
  E2E smoke geçer.

### P0.8 Secret operasyonu (F14) — SEC-01

> **Durum 2026-11-25:** VM'de `SRE_SETTINGS_KEY` etkin (`store_available: True`); secret girişi ve rotasyon kullanıcıda (§11 karar 5).

- **Değişiklik (kod değil, operasyon + doküman):** VM'de `SRE_SETTINGS_KEY` üretimi `scripts/gen-sre-platform-env.sh`
  ile; `docs/security/SECRETS_INVENTORY.md` (secret adı, sahibi, konum, rotasyon periyodu — değer içermez);
  yerel `deploy/out/` dosyalarının silinmesi/şifrelenmesi önerisi. **Canlı credential rotasyonu kullanıcı onayıyla.**
- **Kabul:** Settings > Security & Audit `secret_store: enabled`; envanter belgesi.

### P0.9 Temel CI ve secret taraması (F9, F18) — SDLC-01

- **Değişiklik (karar 3: GitHub Actions):** `.github/workflows/ci.yml`: backend + sre-agent pytest (PostgreSQL
  service), frontend `tsc`/`vitest`/`build`; `gitleaks` (test fixture'ları için `.gitleaks.toml` allowlist —
  fixture'lar sentetik olduğu doğrulanarak), `pip-audit`, `npm audit --omit=dev`. Kök `SECURITY.md` (bildirim
  kanalı, desteklenen sürüm). Push/CI etkinleştirme kullanıcı onayıyla.
- **Kabul:** CI yeşil; kasıtlı eklenen sahte token içeren dal kırmızı.

## 4. P1 — yüksek

> **Durum (2026-11-25):** P1.1, P1.2, P1.3, P1.6, P1.7, P1.8, P1.9 kod + test tamam, APP_VM'e deploy edildi; P1.4 ertelendi (karar 2); P1.5 uyumsuz değişiklik — kullanıcı onayı bekliyor. Kanıt, plandan sapmalar ve kalan riskler: `SECURITY_P1_EVIDENCE.md`.

### P1.1 Merkezi policy katmanı (F8) — AGT-01

- `sre_platform/security/policy.py`: `Decision(ALLOW|DENY|REQUIRE_APPROVAL, reason_code, policy_version)`;
  `decide(PolicyContext)` saf fonksiyon (kimlik, rol/permission, trigger, connector, tool, risk, ayar revizyonu).
- Enforcement noktaları: run başlatma/resume (API, worker, watch), `gateway_factory`, LLM profil seçimi, Slack/LangSmith
  sink'leri. Mevcut `check_allowed` içeriği taşınır, davranış değişmez.
- Karar logu: DENY ve REQUIRE_APPROVAL audit'e; `policy_version` = Settings revizyonu.
- Fail-closed: policy değerlendirmesi hata verirse DENY.
- **Kabul (T05, T16):** policy yüklenemezse run başlamaz; karar `reason_code` ile audit'te; mevcut testler değişmeden geçer.

### P1.2 Permission modeli (X8) — IAM-03

- Kodda sabit eşleme: `viewer → {record:read}`, `sre → +{agent:run, incident:write, approval:request}`,
  `admin → +{settings:*, connector:*, user:*, approval:decide, memory:delete}`. `deps.require_perm("…")`.
- Önce mevcut `require(role)` ile paralel; rota envanteri testi her rotanın bir permission'a bağlı olduğunu doğrular.
- **Kabul:** matris testi (rol × uç → beklenen kod); davranış farkı yok.

### P1.3 F5 kararının uygulanması — IAM-03, MEM-01

> **Karar (a):** ekip geneli görünürlük kalır; yalnız dokümantasyon + arayüz bilgilendirmesi. Migration yok.

- (b) seçilirse: migration `0007_investigation_visibility` (`visibility` varsayılan `org`; sohbetten doğanlar `owner`),
  liste/detay/evidence/RCA/export/episode sorgularına filtre. (a) seçilirse: `docs/security` altına veri paylaşım
  kararı, UI'da "Bu soru ekibe görünür" bilgilendirmesi.
- **Kabul (T03):** (b)'de kullanıcı B, A'nın sohbet investigation'ını 404 alır; admin görür.

### P1.4 HttpOnly cookie geçişi (X2) — IAM-02

> **Ertelendi (karar 2):** TLS olmadan `Secure` cookie kullanılamaz; TLS kararı değişirse uygulanır.

- Refresh token HttpOnly + `Secure` + `SameSite=Strict` cookie, access token bellekte (localStorage değil), CSRF için
  `SameSite=Strict` + `Origin` kontrolü. `services/sse.ts` aynı mekanizmayı kullanır.
- **Kabul:** `localStorage`'da token yok; sekme yenilemede oturum sürer; CSRF negatif testi.

### P1.5 DB rol ayrımı (F6, X5) — INF-01, AUD-01

- Migration `0008_db_roles` (owner tarafından): `sre_app` (DML; `audit_events` için yalnız INSERT/SELECT),
  `sre_worker` (gerekli tablolar), `sre_readonly`. Migrate owner rolüyle, backend/worker yeni rollerle bağlanır.
- PostgreSQL bağlantısında `sslmode=require` seçeneği (internal ağda opsiyonel, flag).
- **Rollback:** env'de eski kullanıcıya dön; roller kalabilir.
- **Kabul (T14):** `sre_app` ile `DELETE FROM sre.audit_events` → permission denied.

### P1.6 LLM sertleştirme (F15) — LLM-01, AGT-03

- Per-run toplam token tavanı (`budgets.max_tokens_per_run`), LLM çağrı sayısı tavanı; aşımda deterministik yanıt +
  audit `limit.exceeded`.
- Aylık bütçe için LangSmith'ten bağımsız sayaç: her çağrının `usage`'ı `sre.llm_usage`'a (model, tokens, run_id,
  request_id) yazılır.
- Model allowlist (Settings, admin); prompt şablon sürümü raporda/loglarda.
- **Kabul (T08, T11):** LLM kapalıyken dış istek sayısı 0 (httpx mock); tavan aşımında kısmi sonuç, audit kaydı.

### P1.7 MCP sertleştirme (F16) — MCP-01/03

- Keşifte tool tanımlarının (ad, açıklama, inputSchema, annotations) SHA-256'sı saklanır; çalışma anında farklıysa
  connector `needs_review` → yeni run'lar `McpUnavailable` + audit.
- Yanıt boyutu sınırı (kayıt sayısı ve bayt; Settings bütçesi) → aşımda kırpma + evidence notu.
- Çağrı anında DQL doğrulaması: zaman aralığı ≤ `max_minutes`, sorgu katalog anahtarından geliyor mu.
- Dynatrace token scope'unun operasyonel doğrulaması (dokümante kontrol listesi; değişiklik kullanıcı onayıyla).
- **Kabul (T05):** fixture sunucu açıklamasını değiştirince run reddedilir; büyük yanıt kırpılır.

### P1.8 DLP genişletme (F17) — DLP-01

- `redact.py`'ye e-posta, IBAN, kredi kartı (Luhn), T.C. kimlik no (checksum), IPv4 (opsiyonel), JWT, bearer token
  kalıpları; sink başına politika (LLM, LangSmith, Slack, log, export) Settings'ten.
- Kalıplar regex + checksum; yanlış pozitifler testle izlenir. Deterministik analiz ham veri üzerinde kalır, yalnız
  dışa giden kopya maskelenir.
- **Kabul (T07):** sentetik PII içeren fixture LLM payload'ında, trace'te, Slack metninde maskeli.

### P1.9 API hataları ve prompt injection testleri (F12) — API-01, AGT-02

- `ValueError` → genel mesaj + correlation ID (alan doğrulama mesajları Pydantic'ten gelmeye devam eder);
  `ChatRoute`/`AnalysisRequest` pydantic modellerinde `extra="forbid"`.
- Sentetik injection fixture seti (log satırında "ignore previous instructions", sahte DQL, sahte tool çağrısı):
  çıktının tool/ayar/secret talep etmediği, skorun değişmediği doğrulanır (LLM çağrısı mock'lu + opsiyonel gerçek eval
  kullanıcı onayıyla).
- **Kabul (T06, T10).**

## 5. P2 — orta

| Adım | Gereksinim | Özet | Kabul |
|---|---|---|---|
| P2.1 OIDC SSO + MFA | IAM-01 | `authlib` code flow + PKCE, issuer/audience/nonce doğrulaması, grup → rol eşlemesi; yerel giriş break-glass (admin, audit'li) | Sahte IdP ile negatif testler |
| P2.2 Service account | IAM-03, T19 | Kapsamlı, süreli API anahtarları (hash'li saklama), permission alt kümesi | Kapsam dışı uç 403 |
| P2.3 SIEM çıkışı | AUD-02, T22 | Audit olaylarının JSON/Syslog/webhook ile iletimi, outbox tablosu + teslim durumu; alarm kuralları (çoklu başarısız login, policy DENY patlaması, egress.denied) | Hedef kapalıyken olay kaybolmaz |
| P2.4 Vault/KMS | SEC-01 | `secrets.resolve` için `vault:` referans tipi; rotasyon/expiry metadata, süresi yaklaşan secret uyarısı | Rotasyon sonrası yeni değer, eski iptal |
| P2.5 Backup/restore | INF-01, T20 | `pg_dump` şifreli yedek işi, restore runbook'u, RPO/RTO | Restore tatbikatı staging'de |
| P2.6 Egress kontrolü | MCP-02 | Host firewall/proxy ile `airlab-agent` ağından yalnız Dynatrace/Anthropic/LangSmith/Slack | İzinsiz host'a bağlantı düşer |
| P2.7 Retention | DLP-01, MEM-01 | Checkpoint/Store için TTL işi; konuşma silindiğinde thread temizliği; audit retention (owner fonksiyonu) | Süresi dolan veri silinir, audit kaydı kalır |
| P2.8 Security & Governance UI | UI-01 | Oturumlarım, permission matrisi (salt okunur), policy kararları, egress/SIEM durumu, approval ayrıntısı; mevcut tasarım bileşenleriyle | Her ekran gerçek API'ye bağlı; viewer'da gizli + backend'de 403 |

## 6. P3 — olgunluk

- SBOM (CycloneDX) ve imaj imzalama/provenance; Trivy imaj taraması CI'da.
- DAST (OWASP ZAP baseline) staging'de.
- Kalıcı AI güvenlik eval seti (injection, data exfiltration, hallucinated evidence) — `sre-agent/eval` altında,
  LLM maliyeti kullanıcı onayıyla.
- Dynatrace/kurumsal MCP için mTLS ve kurumsal CA bundle.
- Air-gap / LLM'siz mod doğrulaması (dış ağ kapalıyken deterministik akış).
- Compliance raporu (kontrol → kanıt eşlemesi bu belgelerden üretilir).

## 7. Bağımlılık grafiği

```
P0.1 TLS ──────────────▶ P1.4 HttpOnly cookie (Secure bayrağı TLS ister)
P0.2 Sessions ─────────▶ P1.4, P2.1 OIDC, P2.8 UI (oturum ekranı)
P0.4 Audit şeması ─────▶ P1.1 Policy (karar logu), P1.5 (append-only rol), P2.3 SIEM
P0.3 Resume fix ───────▶ P1.1 Policy (kontrol taşınır)
P1.2 Permissions ──────▶ P2.2 Service account, P2.8 UI matrisi
P1.5 DB rolleri ───────▶ P2.7 audit retention fonksiyonu
P0.9 CI ───────────────▶ P3 SBOM/DAST
F5 kararı (X4) ────────▶ P1.3
```

## 8. Migration özeti

| Revizyon | Faz | İçerik | Downgrade |
|---|---|---|---|
| 0005_auth_sessions | P0 ✅ | `auth_sessions`, `auth_login_failures` | tabloları siler (herkes yeniden giriş) |
| 0006_audit_extend | P0 ✅ | audit kolonları + UPDATE/DELETE/TRUNCATE append-only tetikleyicileri | tetikleyiciler + kolonlar kaldırılır, satırlar kalır |
| ~~0007_investigation_visibility~~ | P1 (X4-b) | yapılmadı — karar 1 (ekip geneli) | — |
| 0008_db_roles | P1 | DB rolleri ve GRANT'lar | roller kalır; env eski kullanıcıya döner |
| 0007_llm_usage | P1.6 ✅ | `llm_usage` (tool özetleri connector `capabilities` JSON'unda; kolon gerekmedi) | tablo silinir |

Her migration için `test_settings.py`'deki gibi up → down → up testi eklenir; migration'lar staging verisinin
kopyası üzerinde önce prova edilir.

## 9. Genel rollback prosedürü

1. Aktif chaos deneyi yokken (`/api/v2/experiments` → `active: null`) işlem yapılır.
2. `docker compose --profile platform run --rm sre-migrate migrate --downgrade <önceki revizyon>`.
3. `SRE_IMAGE_TAG=<önceki>` ile `up -d sre-backend sre-worker sre-frontend`.
4. Compose/nginx değişiklikleri git revert ile; sertifika ve env dosyaları VM'de yedeklenmiş kopyadan.
5. Smoke: `/api/health`, giriş, bir chat turu, Settings sayfası.

## 10. Test planı (T01–T22 → faz)

| Faz | Testler |
|---|---|
| P0 | T01 (mevcut), T02, T04, T09, T12, T14, T15 (mevcut), T18, T21 (mevcut) |
| P1 | T03, T05, T06, T07, T08, T10, T11, T13 (argüman bağlama yürütme eklendiğinde), T16, T17 |
| P2 | T19, T20, T22 |

## 11. Kararlar (2026-11-25)

| # | Konu | Karar | Plana etkisi |
|---|---|---|---|
| 1 | X4 — sohbet kaynaklı investigation görünürlüğü | **Ekip geneli kalır** (seçenek a) | P1.3 → yalnız dokümantasyon + arayüz bilgilendirmesi ("Bu analiz ekibe görünür"); migration `0007` yapılmaz. F5 kabul edilmiş tasarım kararıdır. |
| 2 | P0.1 — TLS | **TLS yok** (kabul edilmiş risk) | Aşağıdaki §11.1 telafi edici kontroller uygulanır; HSTS ve `Secure` cookie yapılamaz; P1.4 cookie geçişi ertelenir. |
| 3 | P0.9 — CI | **GitHub Actions** (`origin` = GitHub deposu) | Workflow dosyası hazırlanır; push her seferinde ayrıca onaylanır. |
| 4 | P2.1 — IdP | Karar verilmedi | P2.1 beklemede; P0/P1 etkilenmez. |
| 5 | Credential yönetimi | **Platformdan yönetilecek**; rotasyonu kullanıcı yapar | P0.8 tamamlandı: VM'de `SRE_SETTINGS_KEY` üretildi (değer gösterilmedi, env yedeği alındı), backend/worker yeniden başlatıldı, `store_available: True`. `scripts/gen-sre-platform-env.sh` yeni kurulumlarda anahtarı üretir. Kalan: kullanıcı Settings > Security & Audit'ten secret'ları girer, connector/LLM profillerini `secret:` referansına çevirir, eski anahtarları sağlayıcı panelinden iptal eder, `deploy/out/` temizlenir. **`SRE_SETTINGS_KEY` kaybolursa saklanan secret'lar çözülemez — VM dışında güvenli bir yere yedeklenmeli.** |

### 11.1 TLS olmadan telafi edici kontroller (karar 2)

TLS olmadan parola ve bearer token ağda okunabilir; bu risk ortadan kalkmaz, yalnız azaltılır:

- `sre-frontend` portu yalnız gerekli arayüze bağlanır (`SRE_UI_BIND_IP`), host firewall ile yalnız yönetim ağı/VPN.
- Oturum süreleri kısaltılır (P0.2: idle 30 dk, mutlak 8 sa) ve sunucu tarafı iptal eklenir — çalınan token'ın
  kullanım süresi sınırlanır.
- Login throttle kalıcı hale gelir (P0.6); başarısız girişler audit'e yazılır (P0.4).
- Token `localStorage`'da kalır (cookie'ye geçiş `Secure` bayrağı olmadan anlamlı kazanç sağlamaz); CSP korunur.
- Gereksinim karşılığı: IAM-01/SEC-02 TLS maddeleri **risk kabulü** olarak işaretlenir; ileride TLS eklenirse P0.1
  olduğu gibi uygulanabilir.

# Güvenlik P2: Kanıt

Durum: P2.8, P2.2, P2.3 ve P2.4 kodlandı, test edildi ve 2026-11-25'te APP_VM'ye deploy edildi (alembic 0010).

## P2.8 Security & Governance UI

| Bileşen | Backend | Frontend | Test |
|---|---|---|---|
| Kendi yetkilerim | `GET /api/auth/permissions` (yalnız oturum) | Hesabım → Yetkilerim | `test_my_permissions_reflect_role` |
| Oturumlarım + tek tek kapatma | `GET/DELETE /api/auth/sessions` (P0) | Hesabım → Oturumlarım | P0 testleri |
| Diğer oturumları kapat | `POST /api/auth/sessions/revoke-others` (bu oturum korunur; audit `auth.session.revoke_others`) | Hesabım → onay diyaloğu | `test_revoke_other_sessions_keeps_current` |
| Parola değişikliği | `POST /api/auth/password` (P0) | Hesabım → Parola değiştir | P0 testleri |
| Kullanıcı yönetimi | `/api/users` CRUD (`user:manage`) | Settings → Users & Sessions (yalnız admin) | `test_api` |
| Kullanıcının tüm oturumlarını kapat | `POST /api/users/{id}/sessions/revoke` (`user:manage`; admin kendi oturumunu korur; audit `auth.session.revoke_all`) | Kullanıcı düzenleme diyaloğu | `test_admin_revokes_all_sessions_of_user` |
| Güvenlik olayları özeti | `GET /api/settings/audit/summary?hours=` (`audit:read`; `result != ok`, eylem + neden kodu bazında, en çok 50 satır) | Settings → Security & Governance | `test_security_event_summary` |
| Permission matrisi | `/settings/security.permissions` (P1) | Matris tablosu (rol tablosunun yerine) | P1 + vitest |
| DLP ve model allowlist | `/settings/security.dlp`, `.llm_model_allowlist` | Veri koruma kartı | `test_security_event_summary` |
| Audit filtreleri | `GET /api/settings/audit?action=&result=` | Sonuç/eylem filtresi, `reason_code`, `source_ip` | `test_security_p0` |

Notlar:

- Arayüz yetki kararı vermez. Düğmeler yalnız görünürlük içindir, her istek sunucuda `require_perm` ile denetlenir. Users menüsü admin dışındakilere gizlidir; ama rota zaten `user:manage` ister.
- Yeni uçlar route envanter testine eklendi. `revoke-others` yalnız oturum ister ve yalnız çağıranın oturumlarını etkiler.
- Zaman penceresi SQL'e metin olarak gömülmez, parametre olarak geçer (`hours`: 1–720 arası).

## P2.2 Service account API anahtarları

**Biçim ve saklama**
- Anahtar biçimi `srek_<prefix8hex>_<secret>`; secret 256 bit rastgele.
- DB'de (`sre.api_keys`, migration 0008) yalnız `HMAC-SHA256(JWT secret, secret)` tutulur. Düz anahtar yalnız oluşturma cevabında bir kez döner.
- Doğrulama sabit zamanlı karşılaştırma ile yapılır. Bilinmeyen prefix'te de aynı HMAC hesaplanır.

**Deny-by-default kuralları**

| Kural | Uygulama | Test |
|---|---|---|
| Süre sonu zorunlu, 1–365 gün | `ApiKeyCreateIn`, `api_keys.create` | `test_api_key_rejections` |
| Scope yalnız izinli kümeden | `ALLOWED_SCOPES`: record:read, agent:run, incident:write, approval:request, settings:read, audit:read, observability:read, knowledge:manage. Kullanıcı, secret, connector ve LLM yönetimi, settings:write, approval:decide ve memory:delete verilemez | `test_api_key_rejections` |
| Scope ⊆ oluşturanın yetkileri | `api_keys.create` | birim mantığı |
| Etkin yetki = scope ∩ sahibin o anki rolü (her istekte) | `effective_scopes`, `require_perm` → `_check_scope` | `test_api_key_follows_owner` |
| Anahtar yalnız permission isteyen uçlarda geçer; kimlik, oturum, parola ve sohbet uçları reddeder (403, `apikey_not_allowed`) | `api/deps.py`: `authenticate` / `current_user` (yalnız oturum) / `require_perm` / `keyed` | `test_api_key_lifecycle_and_scopes` |
| Anahtar başka anahtar üretemez | `user:manage` scope'u verilemez; ayrıca rotada açık kontrol | aynı |
| İptal, süre sonu, yanlış secret, bilinmeyen prefix ve bozuk biçim → 401 + audit `auth.apikey.rejected` | `api_keys.authenticate` | `test_api_key_rejections` |
| Sahip devre dışı → anahtarlar kalıcı iptal (`owner_disabled`); yeniden etkinleştirme anahtarları canlandırmaz | `api_keys.revoke_owner` | `test_api_key_follows_owner` |
| Sahip başına en çok 20 etkin anahtar | `MAX_ACTIVE_PER_OWNER` | — |
| Anahtarla yapılan işlemler audit'te `actor_type=service`, `session_id=apikey:<prefix>` | `security/audit.py`, `context.api_key` | `test_api_key_lifecycle_and_scopes` |
| Son kullanım zamanı ve IP (dakikada bir yazılır) | `last_used_at`, `last_used_ip` | — |

**Rota değişikliği:** Kayıt okuma uçları (`records.py`, `catalog.py` GET) `current_user` yerine `perm(P.RECORD_READ)` istiyor. Her rolde `record:read` olduğu için oturumla davranış değişmez; anahtar için `record:read` scope'u gerekir. Agent koşu durumu, stream ve iptal uçları `keyed(P.AGENT_RUN)` kullanıyor: oturumla davranış aynı, anahtar `agent:run` ister. Route envanter testi artık `authenticate` bağımlılığını arıyor.

**Arayüz:** Settings → Users & API Keys. Oluşturma diyaloğunda scope seçimi ve süre var. Anahtar bir kez gösterilir ve kopyalanabilir. Listede son kullanım bilgisi var. İptal onay diyaloğuyla yapılır.

**Kalan riskler**
- R-P2.2a: Anahtarlar JWT secret ile özetlenir. JWT secret değişirse tüm anahtarlar geçersiz olur; bu bilinçli bir tercih, rotasyonda yeniden oluşturulmaları gerekir.
- R-P2.2b: Anahtar başına ayrı rate limit yoktur; LLM koşu bütçesi (P1.6) ve eşzamanlı koşu sınırı geçerlidir.
- R-P2.2c: TLS yok (kullanıcı kararı). Anahtar ağda düz metin taşınır; oturum token'ı ile aynı risk.

## P2.3 SIEM iletimi ve güvenlik alarmları

İkisi de **varsayılan kapalı**. Settings › Notifications'tan açılır; durumları Security & Governance ekranındadır. Worker işi `security-ops` 60 saniyede bir çalışır ve ayar kapalıyken hiçbir şey yapmaz.

**SIEM iletimi** (`services/security_ops.py`)
- Outbox olarak append-only `sre.audit_events` ve bir cursor kullanılır (`sre.security_state`, migration 0009); ayrı bir kopya tablo yoktur.
- Teslim at-least-once'tır: 2xx gelmeden cursor ilerlemez. Olay `id`'si alıcıda tekrarı ayıklamak için kullanılabilir. Bir turda en çok 5 batch gönderilir; batch boyutu 10–1000 (varsayılan 200).
- İlk açılışta geçmiş gönderilmez; cursor o anki son id'ye konur.
- Hedef adres secret referansıyla verilir. Kayıtta ve her gönderimde SSRF kontrolü yapılır, redirect izlenmez.
- İsteğe bağlı HMAC-SHA256 imzası `X-SRE-Signature: sha256=…` başlığıyla gönderilir; ayrıca `X-SRE-Batch-Id` eklenir.
- Gövde audit'e yazılırken redakte edilmiştir; gönderimde ayrıca "export" sink'i uygulanır.
- Teslim hatası her turda değil, yalnız durum değişiminde audit'lenir: `siem.delivery_failed` ve `siem.delivery_recovered`. SSRF reddi `egress.denied` olarak yazılır.

**Alarm kuralları** (kodda tanımlı; threshold ve pencere ayardan gelir)

| Kural | Eylemler | Varsayılan threshold |
|---|---|---|
| `login_failures` | `auth.login` (başarısız / engellenen) | 10 |
| `authz_denied` | `authz.denied` | 20 |
| `apikey_rejected` | `auth.apikey.rejected` | 10 |
| `policy_denied` | `tool.denied`, `run.denied`, `egress.denied`, `llm.model_denied`, `llm.budget_exceeded` | 5 |
| `tool_definition_changed` | `connector.tool_changed` | 1 (sabit) |

- Pencere varsayılan 15 dakikadır. Aynı kural pencere içinde bir kez alarm üretir (cooldown).
- Alarm audit'e `security.alert` olarak yazılır (`result=alert`, `reason_code=<kural>`); Slack webhook tanımlıysa Slack'e de gider. Alarmlar güvenlik olay özetinde görünür ve SIEM'e de iletilir.

**Testler** (`test_security_p2.py`)
- `test_security_ops_disabled_by_default`: kapalıyken no-op; referanssız açma 422 döner.
- `test_siem_forwarding_at_least_once`: geçmiş gönderilmez, batch'lere bölünür, sıralı ve tekrarsızdır. Hatada cursor ilerlemez ve tek audit yazılır; düzelince kayıpsız devam eder.
- `test_siem_signature_and_ssrf`: HMAC doğrulanır; iç ağ adresine kayıt reddedilir.
- `test_security_alert_rules_and_cooldown`: threshold altında alarm yok, aşınca bir alarm çıkar; cooldown, audit, olay özeti ve Slack kontrol edilir.

**Kalan riskler**
- R-P2.3a: Hedef yalnız HTTPS webhook (JSON). Syslog/CEF adapter'ı yok.
- R-P2.3b: TLS'siz iç ağda SIEM'e giden trafik, hedef adres `https` olduğu sürece şifrelidir. Platformun kendi arayüzü TLS'sizdir (kullanıcı kararı).
- R-P2.3c: Alarm kuralları kodda tanımlıdır; arayüzden yalnız threshold ve pencere değişir.

## P2.4 Secret rotation ve süre sonu metadata'sı

Vault yok; metadata `sre.secrets` tablosunda tutulur (migration 0010: `expires_at`, `rotation_days`, `description`). Değerler değişmeden Fernet ile şifreli kalır.

| Kural | Uygulama | Test |
|---|---|---|
| Süresi dolan secret çözülmez (fail-closed); connector, LLM ve bildirim çağrıları hata verir | `secrets.resolve` | `test_secret_lifecycle_metadata_and_expiry` |
| Süresi dolmuş secret, yeni tarih verilmeden ya da süre sonu açıkça kaldırılmadan döndürülemez; eski süre sonu sessizce taşınmaz | `secrets.put` | aynı |
| Rotation süresinin geçmesi (varsayılan 90 gün; mevcut secret'lara da uygulanır) yalnız uyarıdır, kullanımı engellemez | `secrets.lifecycle` | aynı |
| Süre sonu gelecekte olmalı ve saat dilimi içermeli; rotation 1–3650 gün; açıklama ≤256 karakter | `_check_meta`, `SecretMetaIn` | aynı (negatif) |
| Yalnız metadata değişikliği `PATCH /settings/secrets/{name}` (`secret:manage`); öncesi ve sonrası audit'lenir (`secret_metadata`), değer audit'e girmez | `patch_secret_meta` | aynı |
| Durum öncelik sırası: expired > expiring (≤14 gün) > rotation_due > ok | `lifecycle` | aynı |
| Secret hijyeni alarmı: süresi dolmuş, yaklaşan ya da rotation süresi geçmiş secret varsa günde en çok bir `security.alert` (`secret_hygiene`); alarmlar açıksa | `security_ops.evaluate_alerts` | `test_secret_hygiene_alert_once_per_day` |
| **P2.3 düzeltmesi:** "kullanımda" kontrolü tüm settings `*_ref` alanlarını kapsar (SIEM URL ve imza secret'ları dahil); kullanılan secret silinemez | `secret_usage` | `test_secret_usage_covers_all_settings_refs` |

**Arayüz:** Secrets tablosunda durum rozeti, açıklama, süre sonu ve rotation tarihi gösterilir. Ekleme ve döndürme diyaloğunda süre sonu, rotation ve açıklama alanları var. "Politika" diyaloğu yalnız metadata'yı değiştirir. SIEM kartında secret hijyeni özeti yer alır. Ortam değişkeni secret'ları deployment tarafından yönetilir; metadata'ları yoktur.

**Kalan riskler**
- R-P2.4a: Otomatik rotation yok (Vault ya da sağlayıcı API'si yok). Rotation, değerin elle girilmesiyle yapılır; platform yalnız hatırlatır ve süresi doleni engeller.
- R-P2.4b: `env:` secret'larının süre sonu ve rotation bilgisi izlenmez.
- R-P2.4c: Süresi dolan secret çözülmediğinde ayrı bir audit kaydı yazılmaz; günlük hijyen alarmı ve çağıran bileşenin hatası görünür.
- R-P2.4d: Son kullanım zamanı tutulmaz (okuma yolunda yazma yükü olmaması için).

## Regresyon

- P2.8 + P2.2 (deploy edilen): backend 126 geçti / 1 atlandı, frontend 41/41.
- P2.4 sonrası (CI ile aynı koşullar): backend 134 geçti (terminoloji testinin frontend kontrolü dahil; migration testi 0010 dahil), sre-agent 126 geçti; frontend `tsc` temiz, vitest 43/43, `vite build` başarılı.
- P2.3 sonrası: backend 130 geçti, 1 atlandı (migration testi 0009 ve geri alma kontrolü içeriyor); frontend `tsc` temiz, vitest 42/42, `vite build` başarılı.

## Canlı doğrulama (2026-11-25)

- Rollback image'ları: `airlab/sre-platform-backend:pre-p2`, `airlab/sre-platform-frontend:pre-p2`.
- `alembic_version` = 0008, servisler healthy.
- Kimliksiz `GET /api/api-keys`, `/api/auth/permissions`, `/api/settings/audit/summary` → 401. `/api/docs` → 404.
- Sahte `srek_` anahtarı → 401 ve audit'te `auth.apikey.rejected` / `apikey_unknown`.
- Frontend bundle'ında "Oturumlarım" ve "Users & API Keys" var.
- Geri alma: `docker compose run --rm sre-migrate` yerine `--downgrade 0007` ve image'ları `:pre-p2`'den geri etiketleme.

**P2.3 canlı doğrulama (2026-11-25):** Rollback image'ları `:pre-p23`. `alembic_version` = 0009. `security-ops` işi etkin (60 sn); ilk koşu SUCCEEDED, sonuç `{"siem": "disabled", "alerts": []}` (varsayılan kapalı). Kimliksiz `/api/settings/security` → 401. Frontend bundle'ında SIEM kartı var.

**P2.4 canlı doğrulama (2026-11-25):** Rollback image'ları `:pre-p24`. `alembic_version` = 0010, servisler healthy. Kimliksiz `GET /api/settings/secrets` ve `PATCH /api/settings/secrets/{name}` → 401. Frontend bundle'ında politika diyaloğu var. Canlıda saklanan secret sayısı 0 (secret'lar env referansıyla geliyor); bu yüzden metadata migration'ı hiçbir satırı değiştirmedi.

**CI notu (2026-11-25):** P2.3 commit'i `9f0b12a` CI'da kırıldı. `test_terminology` frontend metinlerinde "gecikme" kelimesini yakaladı (politika gereği "latency" olmalı). Yerel VM harness'ı backend imajında frontend kaynağı olmadığından bu kontrolü atlıyordu. Düzeltme ifadeleri "rotation süresi geçmiş" ve "bekleyen olay" olarak değiştirdi. Push öncesi doğrulama artık CI ile aynı koşullarda yapılıyor: env dosyası yok, tek kullanımlık test PostgreSQL'i ve frontend kaynağı dahil. Sonuç: backend 134/134, sre-agent 126/126, frontend 43/43. Canlıdaki P2.3 işlevini etkilemiyordu; yalnız metin politikası ihlaliydi.

## Kalan P2 maddeleri

| Madde | Durum |
|---|---|
| P2.2 Service account API anahtarları | Deploy edildi (alembic 0008) |
| P2.3 SIEM outbox ve alarm kuralları (varsayılan kapalı) | Deploy edildi (alembic 0009) |
| P2.4 Secret rotation/expiry metadata | Deploy edildi (alembic 0010) |
| P2.7 Retention | Sırada; silme varsayılanları muhafazakâr olacak |
| P2.1 IdP/SSO | Karar bekliyor (IdP seçilmedi) |
| P2.5 Yedekleme | Karar bekliyor (hedef konum ve şifreleme anahtarı) |
| P2.6 Host firewall | Onay bekliyor (VM ağ değişikliği) |

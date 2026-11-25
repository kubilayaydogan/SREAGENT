# Güvenlik P2: Kanıt

Durum: P2.8 ve P2.2 kodlandı, test edildi ve 2026-11-25'te APP_VM'ye deploy edildi (alembic 0008).

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

## Regresyon

- Backend: 126 geçti, 1 atlandı. P2.8 için 4, P2.2 için 3 yeni test; migration testi 0008 ve geri alma kontrolü içeriyor.
- Frontend: `tsc` temiz, vitest 41/41, `vite build` başarılı.

## Canlı doğrulama (2026-11-25)

- Rollback image'ları: `airlab/sre-platform-backend:pre-p2`, `airlab/sre-platform-frontend:pre-p2`.
- `alembic_version` = 0008, servisler healthy.
- Kimliksiz `GET /api/api-keys`, `/api/auth/permissions`, `/api/settings/audit/summary` → 401. `/api/docs` → 404.
- Sahte `srek_` anahtarı → 401 ve audit'te `auth.apikey.rejected` / `apikey_unknown`.
- Frontend bundle'ında "Oturumlarım" ve "Users & API Keys" var.
- Geri alma: `docker compose run --rm sre-migrate` yerine `--downgrade 0007` ve image'ları `:pre-p2`'den geri etiketleme.

## Kalan P2 maddeleri

| Madde | Durum |
|---|---|
| P2.2 Service account API anahtarları | Kodlandı ve test edildi (yukarıda) |
| P2.3 SIEM outbox ve alarm kuralları (varsayılan kapalı) | Sırada |
| P2.4 Secret rotation/expiry metadata | Sırada |
| P2.7 Retention | Sırada; silme varsayılanları muhafazakâr olacak |
| P2.1 IdP/SSO | Karar bekliyor (IdP seçilmedi) |
| P2.5 Yedekleme | Karar bekliyor (hedef konum ve şifreleme anahtarı) |
| P2.6 Host firewall | Onay bekliyor (VM ağ değişikliği) |

# Settings modülü

`/settings` ekranı ve `/api/settings/*` API'si: platformun çalışma zamanı yapılandırması. Gereksinim kaynağı:
`docs/specs/SETTINGS_SPECIFICATION.md`. Bu belge mevcut mimariye göre ne uygulandığını, neyin bilerek **uygulanmadığını** ve
işletim notlarını anlatır.

## 1. Mevcut durum analizi (geliştirme öncesi)

| Konu | Önce | Şimdi |
|---|---|---|
| Yapılandırma kaynağı | Ortam değişkenleri (`PlatformSettings`, `airlab_agent.config`), `defaults.yaml`, `kv.llm_model` | Aynı kaynaklar **bootstrap** olarak + `sre.settings_sections` (admin değerleri) + deployment lock'ları |
| Değişiklik | Env düzenleme + restart | Arayüzden, revizyonlu ve audit'li; çoğu alan restart gerektirmez |
| Investigation girişi | `InvestigationRunner.run` (tek nokta) | Aynı nokta; koşu başında `RunConfig` kurulur, `investigations.config_snapshot`'a yazılır |
| MCP | Tek `McpGateway` (env URL + token) | `sre.connectors`: analiz kaynağı connector + onaylı tool listesi |
| LLM | `CLAUDE_MODEL` / `CLAUDE_RCA_MODEL` + üst bar seçimi (kv) | `sre.llm_profiles` + görev yönlendirmesi (chat / rca) + üst bar seçimi (`llm.model_override`) |
| Zamanlayıcı | Worker açılışında env'den | Settings > Scheduling; kayıt anında iş tablosu güncellenir |

## 2. Öncelik

```
deployment lock (SRE_SETTINGS_LOCKS)  >  admin değeri (settings_sections)  >  ortam / yapılandırma dosyası  >  uygulama varsayılanı
```

Her alanın kaynağı arayüzde rozetle gösterilir (Locked / Admin / Ortam / Varsayılan). Admin değeri olan alanda
devralınan değer görünür ve "devralınana dön" ile admin değeri silinir (`reset`). Yalnız bootstrap'tan farklı değerler
saklanır. Bu sayede env değişikliği admin değeri olmayan alanlara doğrudan yansır.

Uygulama zamanı (alan rozeti):

- **Sonraki koşu** (varsayılan): koşu başında okunur; süren koşu etkilenmez.
- **Sonraki tur**: worker'ın zamanlayıcı turu (≤5 sn).
- **Restart gerekli**: `max_concurrent_runs`, `watch_confirm_windows`, `watch_cooldown_minutes`, `langsmith_project`.
  Kaydedilen değer süreç açılışındakinden farklıysa "Restart bekliyor" görünür.

## 3. Bölümler

| Bölüm | İçerik | Notlar |
|---|---|---|
| General & Appearance | Platform adı, timezone, tarih biçimi, dil | Tema kullanıcıya özel (tarayıcı). Dil yalnız `tr` (başka çeviri yok). Varsayılan timezone `Europe/Istanbul` (önceki sabit davranış). |
| Agent | enabled, execution mode, incident-triggered, pencereler, Timeout, eşzamanlılık, ek evidence turu, `recursion_limit`, query bütçesi/Timeout/retry, LLM açık/kapalı | `enabled=false` → yeni koşu yok (409 `agent_disabled`), kayıtlar okunur. `manual` → health check atlanır; `scheduled` → sohbet/manuel investigation kapalı. |
| Scheduling | Health check, watch, LangSmith senkronu (aralık tabanlı) | İş tablosu canlı; **Şimdi çalıştır** (admin). Çalışan iş için ikinci koşu yok (409). |
| Risk Scoring | Ağırlıklar (toplam 1), normalizasyon aralıkları, bantlar, min. coverage | **Önizleme**: sabit 4 senaryo, mevcut deterministik puanlayıcı (`airlab_agent.scoring`). Geçmiş skorlar yeniden hesaplanmaz. |
| MCP Servers | Connector CRUD, bağlantı testi (`initialize`), tool keşfi (`tools/list`), tool onayı | Yalnız Streamable HTTP. Tool kimliği `connector/tool`. Read-only politikada yazma/yıkıcı tool onaylanamaz. |
| LLM Providers | Profil CRUD, test (`models.retrieve`, prompt yok), model listesi, chat/RCA yönlendirmesi, aylık bütçe | Yalnız Anthropic adapter'ı doğrulanmış; diğer provider'lar "adapter yok" olarak listelenir ve oluşturulamaz. |
| Observability | LangSmith açık/kapalı, project, sampling oranı, environment etiketi, bağlantı testi | Redaction her zaman açık. OpenTelemetry desteklenmiyor (SDK kurulu değil). |
| Notifications | Slack açık/kapalı, webhook **secret referansı**, risk threshold, test mesajı | Test mesajı `[TEST]` etiketlidir, evidence içermez. |
| Retention & Memory | Kategori başına saklama günleri, checkpointer/store durumu, thread checkpoint silme | Checkpoint silme onay metni ister, aktif koşuda reddedilir, audit'e yazılır. |
| Security & Audit | Secret yöneticisi, roller, lock/allowlist görünümü, audit log | Secret değeri hiçbir cevapta yok. |

## 4. Güvenlik

- **RBAC (sunucu tarafı):** okuma `sre`, değişiklik / test / keşif / run-now / silme `admin`. `viewer` yalnız
  `GET /api/settings/public` (görüntü tercihleri).
- **Secret'lar:** ayarlar yalnız referans tutar: `secret:ad` (Fernet ile şifreli `sre.secrets`, anahtar
  `SRE_SETTINGS_KEY`) veya `env:AD` (yalnız izin listesindeki değişkenler). Değerler API'de, audit'te ve loglarda
  görünmez (redaction). Rollback secret değeri geri getirmez.
- **SSRF:** connector endpoint'i, LLM `base_url` ve Slack webhook'u `netguard.check_url`'den geçer: https zorunlu,
  URL'de kullanıcı bilgisi yasak, çözülen TÜM adresler public olmalı (loopback, private, link-local/metadata,
  multicast, reserved reddedilir). İstisna: `SRE_OUTBOUND_ALLOWLIST`. Redirect takip edilmez, TLS doğrulaması açık.
  Kalan risk: DNS rebinding (kontrol ile bağlantı arasında adres değişirse) — allowlist'i dar tutun.
- **Test uçları** kullanıcı + hedef başına 5 sn'de bir (429).
- **Eşzamanlı düzenleme:** her PATCH `revision` ister; uyuşmazlıkta 409 `revision_conflict` (+ `current_revision`).
  Arayüz taslağı korur ve yenilemeyi önerir.
- **Audit:** `sre.audit_events` (actor, eylem, bölüm, hedef, revizyon, redakte before/after, sonuç). Reddedilen
  değişiklikler (lock, doğrulama) ayrı transaction'da yazılır.

## 5. Koşu başına config snapshot

`settings/runtime.run_config` her investigation (ve sohbet turu) başında:

1. etkili ayarlardan agent `cfg`'sinin **kopyasını** kurar (windows, budgets, score) — global durum değişmez,
2. analiz kaynağı connector'ından gateway fabrikası kurar (devre dışı / tool onaysız / secret çözülemez → koşu
   `McpUnavailable` ile kısmi rapor üretir, sahte veri yok),
3. LLM görev profillerini ve bütçeyi değerlendirir (bütçe aşıldı / profil kapalı → o görevde deterministik açıklama,
   koşu notunda yazılı),
4. LangSmith sampling kararını verir (sampling dışı koşuda alt LLM çağrıları da trace göndermez),
5. snapshot'ı (`revisions`, etkili agent/risk değerleri, connector ve profil revizyonları, `hash`; secret yok)
   `investigations.config_snapshot`'a yazar. Investigation detayında "Config" satırında hash ve revizyonlar görünür.

Not: LLM profilleri süreç içindeki paylaşılan istemciye koşu başında uygulanır. Aynı anda iki koşu farklı ayar
revizyonlarıyla başlarsa LLM parametreleri son başlayanınkidir (diğer tüm değerler koşuya özeldir).

## 6. Desteklenmeyenler (bilerek)

| İstek | Durum | Neden |
|---|---|---|
| Cron ifadesi | Yok | Zamanlayıcı kütüphanesi (croniter) kurulu değil; aralık tabanlı zamanlama |
| OpenTelemetry export | Yok | OTel SDK/exporter kurulu değil |
| OpenAI, Gemini, Azure OpenAI, Ollama, OpenAI-compatible | Listelenir, oluşturulamaz | Doğrulanmış adapter yok |
| Açık LLM fallback zinciri | Yok | Yalnız sağlayıcının sunucu tarafı refusal fallback'i; başka sağlayıcıya sessiz geçiş yok |
| Teams, SMTP, genel webhook | Yok | Adapter yok |
| stdio / SSE MCP transport | Yok | Arayüzden komut çalıştırılmaz; SSE eski transport |
| SQLite / InMemory checkpointer | Yok | Backend ve worker ayrı süreç; kalıcılık gerekir. Backend değişimi canlı yapılamaz |
| Otomatik checkpoint retention | Yok | Thread bazında elle silme var |
| Çoklu dil | Yok | Arayüz yalnız Türkçe; teknik terimler İngilizce |

## 7. Kurulum, migration ve rollback

Yeni ortam değişkenleri (`.env.example`): `SRE_SETTINGS_KEY`, `SRE_SETTINGS_LOCKS`, `SRE_OUTBOUND_ALLOWLIST`,
`SRE_SECRET_ENV_ALLOWLIST`. Hepsi opsiyoneldir; boşken davranış öncekiyle aynıdır.

```bash
# APP_VM — şema (0002 → 0003): settings_sections, settings_revisions, connectors, llm_profiles, secrets,
# audit_events tabloları + investigations.config_snapshot
cd /opt/airlab/app-vm
docker compose --profile platform build sre-backend sre-worker sre-frontend
docker compose --profile platform run --rm sre-migrate          # alembic upgrade head
docker compose --profile platform up -d sre-backend sre-worker sre-frontend
```

İlk açılışta (idempotent):

- env'deki Dynatrace MCP'den `dynatrace` connector'ı (analiz kaynağı, `env:DT_PLATFORM_TOKEN`, `execute-dql` onaylı),
- env'deki Claude ayarlarından `claude-chat-env` / `claude-rca-env` profilleri (görev yönlendirmesi boş = ortam;
  davranış değişmez),
- eski `kv.llm_model` seçimi `llm.model_override`'a taşınır.

**Rollback:** ayar düzeyinde Revizyonlar penceresinden (yeni revizyon olarak). Şema düzeyinde:

```bash
docker compose --profile platform run --rm sre-migrate migrate --downgrade 0002   # alembic downgrade
```

Downgrade settings tablolarını ve `config_snapshot` sütununu **siler** (admin değerleri, connector/profil kayıtları,
saklı secret'lar ve audit kayıtları kaybolur); üst bar model seçimi ortam varsayılanına döner. Önceki imaja dönmeden
önce gerekiyorsa `sre.audit_events` dökümünü alın.

## 8. Test

- Backend: `tests/test_settings.py` — öncelik/kaynak, lock, RBAC, 409/422, rollback, audit redaction, secret
  maskeleme, SSRF kuralları, zamanlayıcı senkronu, run-now çift koşu koruması, risk önizleme determinizmi, config
  snapshot, agent kapalıyken 409, devre dışı connector → `McpUnavailable`.
- Frontend: `src/test/settings.test.tsx` — rozetler, lock'lu alan, yalnız değişen alanın gönderilmesi, 409'da taslağın
  korunması, salt okunur rol, timezone/tarih biçimi.

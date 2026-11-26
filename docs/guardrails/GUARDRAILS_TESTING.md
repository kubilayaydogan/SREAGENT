# Guardrails: Test

## Test setleri

| Set | Dosya | Kapsam |
|---|---|---|
| Engine / evaluator (sre-agent) | `sre-agent/tests/test_guardrails.py` (27 test + parametrize) | Katalog ve mandatory enforce; disabled mandatory'nin yine çalışması; secret / PII tespiti ve SRE metninde false positive olmaması; custom pattern doğrulaması (ReDoS, boş eşleşme, backreference); prompt injection; unsupported request uyarısı; read-only DQL, bilinmeyen server, time range, dangerous operation; RBAC rolünün bağlamdan okunması; write-capable → approval ve action_hash; MCP yanıtında indirect injection ve secret; 25 paralel çağrıda atomik sayaç (tam olarak 15 engel); loop detection; kill switch; `guard_node` sync / async; risk score integrity (skor değişmez, not eklenir); evidence yetersizken root cause; monitor modu; deny-overrides; fail-closed / fail-open ve config ile fail_mode; snapshot'ta ham secret olmaması; StreamGuard (parça sınırına bölünen secret, kelime kelime akış, Authorization / kart grupları); sink kayıtları; kapsam eşleşmesi; system prompt protection'ın platform sarmalayıcısını koruması |
| Backend modül | `sre-platform/backend/tests/test_guardrails_module.py` (7 senaryo) | Policy yönetimi, RBAC, sürümleme, rollback, mandatory zayıflatma reddi, audit zinciri ve tamper denemesi; analiz akışının değişmeden tamamlanması ve kayıtların (toplu passed, latency kovaları) ham secret içermemesi; chat'te injection'ın LLM'e gitmeden engellenmesi ve çıktı secret'ının akışta / mesaj kaydında maskelenmesi; kill switch ve config doğrulaması; Policy Tester'ın yan etkisiz olması; ihlal tekilleştirme, triage yaşam döngüsü, approval (four-eyes, gerekçe, süre, replay, parametre uyuşmazlığı); retention |
| Frontend | `sre-platform/frontend/src/test/guardrails.test.tsx` | `jsonDiff`; Overview'da gerçek sayılar ve ölçülmeyen latency "—"; mandatory policy'de mode / durum değiştirilemez (lock) ve rollback yalnız eski sürümde; admin olmayan için salt okunur uyarısı; approval'da dört göz (kendi isteği için yalnız iptal) ve sre rolünde karar düğmesi olmaması; etiket yokken FP / FN "—" |
| Regresyon | Mevcut tüm backend (176) ve sre-agent (198) testleri | Graph akışı, deterministik skor, MCP kayıtları, Agent Trace, Context Windows, Memory, Middleware, Agent Builder, terminology |

## Çalıştırma

CI ile aynı koşul: üretim env dosyası olmadan, tek kullanımlık PostgreSQL ile.

```sh
# sre-agent
cd sre-agent && python -m pytest tests -q
# backend (SRE_TEST_DATABASE_URL tek kullanımlık test veritabanını gösterir)
cd sre-platform/backend && python -m pytest tests -q
# frontend
cd sre-platform/frontend && npx tsc -b --noEmit && npx vitest run && npx vite build
```

Test fixture'ları (`conftest.py`) guardrail süreç durumunu (rate limit penceresi, engine cache, kill switch, fallback)
her testten sonra sıfırlar; audit tablosunun append-only trigger'ı yalnız test temizliğinde geçici olarak kapatılır.

## Policy Tester ile doğrulama

1. Guardrails › Policy Tester'da aşama seçin, örnek senaryo yükleyin veya metin / JSON girin.
2. "Beklenen karar" ve "saldırı" etiketi verilen koşular Analytics'teki FP / FN oranına girer; etiketsiz koşular girmez.
3. Tester dry-run'dır: tool ve LLM çağrılmaz, değerlendirme kaydı, ihlal ve approval yazılmaz.
4. Bir policy'yi enforce'a almadan önce monitor modunda çalıştırıp Analytics › "Monitor vs enforce" tablosunda etkisini
   görün; ihlalleri triage edip false positive olarak işaretleyin.

## Bilinen sınırlar

* FP / FN oranı etiketli veri olmadan hesaplanmaz; üretimde anlamlı oran için etiketli bir saldırı / normal girdi seti
  gerekir (Policy Tester geçmişi bu set için kullanılabilir).
* Injection tespiti kural tabanlıdır; yeni kalıplar için `detect.INJECTION` listesi ve testler birlikte güncellenmelidir.
* Rate limit testi süreç içi pencereyi doğrular; çok replikalı dağıtım test edilmedi.

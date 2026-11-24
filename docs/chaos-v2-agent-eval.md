# SRE Agent — Chaos v2 kör değerlendirmesi (2026-11-24)

Amaç: SRE Agent, Chaos Controller v2'nin ürettiği gerçek arızaları **yalnız Dynatrace telemetry'si** ile, deneyin adı/senaryosu
verilmeden teşhis edebiliyor mu?

## Yöntem

| Adım | Ayrıntı |
|---|---|
| Deneyler | `scripts/chaos-agent-eval-runs.py` (APP_VM): baseline 210 s → ramp 30 s → run 270 s → recovery 90 s; case'ler arası 90 s ara. C0 = chaos yok, yalnız sürekli trafik. |
| Agent girdisi | Yalnız zaman pencereleri (`sre-agent/eval/chaos_v2_windows.yaml`): baseline = BASELINING fazı, pencere = RUNNING fazı (tam dakikaya yuvarlanmış). |
| Etiketler | `sre-agent/eval/chaos_v2_labels.yaml`: agent sonuçları görülmeden yazıldı, sonradan değiştirilmedi. Yalnız karşılaştırmada okunur. |
| Agent modu | `eval/run_eval.py --real` — deterministik attribution (LLM kapalı), Dynatrace Remote MCP `execute-dql`, LangSmith kapalı. |
| Saat kontrolü | Her case öncesi/sonrası VM saati harici HTTP Date ile karşılaştırıldı; sapma > 30 s ise case geçersiz. |

**Kör test düzeltmesi (bu çalışmada).** Chaos workload'ları SQL yorumuna `op=chaos-longtx` gibi etiketler yazıyor. Collector bu
değeri `blocker.op` alanında taşıyordu. `strip_fields` yalnız alan adlarını sildiği için değer agent'a ulaşıyordu. `redact.strip_fields`
artık `*op` alanlarındaki `chaos-*` / `lab_*` değerlerini `unlabeled` yapıyor (test: `test_redact_chaos.py`). Canlı raporlarda
doğrulandı: S03'te 3 `blocker_op` değeri `unlabeled`, iş op'ları (`create_booking`, `checkin` …) korunuyor.

## Sonuçlar

Run 2 (tam telemetry, kapsam 15/15) — **6/8 PASS**. Kanıt: `deploy/evidence/chaos-v2-agent-eval/run2/`.

| Case | Chaos (initiator, intensity) | Ölçülen sinyal (baseline → run) | Agent: primary / source | Sonuç |
|---|---|---|---|---|
| C0 | yok | — | yok, puan 0 | PASS |
| S03 long-running transaction | flightops, MEDIUM | lock edge'ler; reservation mean 3.6 → 133 ms | **row_lock** / flightops-service (high) | PASS |
| S04 CPU-intensive queries | baggage, MEDIUM | DB CPU 1.7 → 51.6 % | **db_cpu** / baggage-service | PASS |
| S01 connection pool exhaustion | checkin, HIGH | checkin p95 9 → 1731 ms, pool timeout 44 | **pool_saturation** / checkin-service | PASS |
| S10 connection storm | flightops+baggage, MEDIUM | DB connections 31 → 63 % (max 59/94) | — (mekanizma yok) | **FAIL** |
| S11 application CPU saturation | baggage, HIGH | APP_VM CPU 7.9 → 19.4 %, baggage 0.03 → 0.74 core | — (olay yok) | **FAIL** |
| S13 DB network latency (netem) | checkin, HIGH | checkin p95 7 → 668 ms | checkin etkilendi, mekanizma "açıklanamayan" | PASS |
| S17 cascading DB saturation | baggage, HIGH | DB CPU 2.2 → 83.7 % | **db_cpu** / baggage-service | PASS |

Run 1 (aynı senaryolar, Java OneAgent server span'i yokken) de 6/8 idi (`run1-no-server-spans/`); aynı iki case başarısız oldu.

## Bulgular

1. **Katalogdaki mekanizmalar doğru bulunuyor:** row_lock, db_cpu, pool_saturation. Source app her seferinde doğru; S03 high confidence.
2. **S10 — göreli artış kuralı yok.** Connection kullanımı iki katına çıktı (31 → 63 %), ama `db_conn_ratio` mutlak eşiği 0.80.
   Chaos MEDIUM yoğunluğu eşiğin altında kaldı. Agent "olay yok" dedi; iş latency'si de değişmediği için yanlış alarm yok, ama kaynak baskısı kaçtı.
3. **S11 — container seviyesinde CPU kuralı yok.** `app_vm_cpu` host CPU'ya bakıyor (≥85 % veya +20 puan). baggage container'ı 0.74 core'a
   çıktı, host +11.5 puan. İş latency'si değişmedi.
4. **Network arızası katalog dışında (S13).** Agent etkiyi buldu ve yanlış bir paylaşılan kaynağa (DB/lock/CPU) atfetmedi, ama kök nedeni
   adlandıramadı. Run 1'de aynı semptomu `pool_saturation` olarak gördü (connection'lar daha uzun tutuluyor).
5. **p95 önceliği tail impact'i gizliyor (S03).** Lock isteklerin küçük bir kısmını bekletiyor: reservation mean ×37, p95 değişmedi.
   Span varken agent p95'i kullanıp yalnız baggage'ı (pool timeout) etkilenmiş saydı. Span yokken (run 1) mean'e düşüp üçünü de buldu.
6. **Etkilenen uygulama tanımı iki tarafta farklı.** Chaos controller "affected" kararını 10 s kayan p95 serisinin run p90'ından veriyor
   (tepe değerlere duyarlı). Agent pencere toplamı p95 + en az 20 ms artış istiyor. S04/S17'de iş endpoint'lerinde p95 artışı
   yalnız +3–5 ms olduğu için agent "etkilenen yok" dedi; chaos controller checkin/baggage'ı etkilenmiş saydı.

## Ortam sorunları (bu çalışmada bulundu ve düzeltildi)

| Sorun | Etki | Düzeltme |
|---|---|---|
| Host modern standby'a girince Hyper-V iki VM'i donduruyor; VM saati dakikalar–saatler geri kalıyor | Pencereler kayar; ilk C0 geçersiz sayıldı | Koşu süresince `SetThreadExecutionState` ile host uyanık; `chronyc burst` + `makestep` |
| 12 saatlik saat step'inden sonra Java OneAgent server span göndermeyi bıraktı | p95 yok (run 1) | `systemctl restart oneagent` yetmedi; uygulama container'ları yeniden başlatıldı |
| netem sidecar'ları eski uygulama container'larının namespace'ine bağlı kalmıştı | Network senaryoları (S13–S16, S19) çalışmıyordu; controller ön kontrolde reddetti | Uygulama yeniden oluşturulunca/başlatılınca: `docker compose up -d --force-recreate --no-deps netem-<app>` |
| Platform image'ı (sre-backend/worker) chaos v2 redaction'ından önce derlenmişti | Platformdaki agent `chaos.*` alanlarını görebiliyordu | Platform yeniden derlendi; konteynerde `hidden` + `_mask_op` doğrulandı |

## Öneriler (uygulanmadı)

- `global_connection_pressure` için göreli kural: ratio ≥ 0.6 **ve** baseline'a göre +25 puan.
- Container CPU mekanizması: bir iş uygulamasının container'ı baseline'a göre ≥ +0.5 core artarsa (host eşiğinden bağımsız).
- Impact kararında p95 ile birlikte mean (veya p99) kontrolü: mean ×5 ve +50 ms ise tail impact say.
- Network latency mekanizması: DB/peer çağrı süresi artıyor ama DB CPU/lock/connection normal → "network path" adayı.

Bu öneriler bu eval'deki case'lerle doğrulanırsa overfitting riski taşır; yeni eşikler ayrı bir senaryo setiyle (farklı yoğunluk/initiator) test edilmeli.

## Yeniden çalıştırma

```bash
# APP_VM (host uykuya girmemeli; ~90 dk)
python3 scripts/chaos-agent-eval-runs.py [--only S03,S13]
# yerel / agent ortamı (DT_ENV, DT_PLATFORM_TOKEN), windows.yaml'ı sre-agent/eval/chaos_v2_windows.yaml olarak kopyalayın
python eval/run_eval.py --real --windows chaos_v2_windows.yaml --labels chaos_v2_labels.yaml
```

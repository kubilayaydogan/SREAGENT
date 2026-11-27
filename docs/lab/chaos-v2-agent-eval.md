# SRE Agent — Chaos v2 kör değerlendirmesi (2026-11-24)

> **Not:** Bu belge 2026-11-24 tarihli koşunun kaydıdır. O tarihteki senaryo setinde S01 (connection pool), S11 (uygulama CPU), S13/S14 (network) ve S18 (iş trafiği) vardı. Senaryolar sonradan yalnız PostgreSQL yükü üretecek biçimde yeniden düzenlendi (güncel liste: [chaos-v2-architecture.md](chaos-v2-architecture.md)); bu kimlikler artık farklı yük tiplerini gösterir. Aşağıdaki sonuçlar eski set için geçerlidir; yeni set için değerlendirme henüz koşulmadı.

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

Run 2 (tam telemetry, kapsam 15/15) — **6/8 PASS** (yeni kurallardan önce). Evidence: `deploy/evidence/chaos-v2-agent-eval/run2/`.

| Case | Chaos (initiator, intensity) | Ölçülen sinyal (baseline → run) | Agent: primary / source | Sonuç |
|---|---|---|---|---|
| C0 | yok | — | yok, puan 0 | PASS |
| S03 long-running transaction | flightops, MEDIUM | lock edge'ler; reservation mean 3.6 → 133 ms | **row_lock** / flightops-service (high) | PASS |
| S04 CPU-intensive queries | baggage, MEDIUM | DB CPU 1.7 → 51.6 % | **db_cpu** / baggage-service | PASS |
| S01 connection pool exhaustion | checkin, HIGH | checkin p95 9 → 1731 ms, pool timeout 44 | **pool_saturation** / checkin-service | PASS |
| S10 connection storm | flightops+baggage, MEDIUM | DB connections 31 → 63 % (max 59/94) | — (mechanism yok) | **FAIL** |
| S11 application CPU saturation | baggage, HIGH | APP_VM CPU 7.9 → 19.4 %, baggage 0.03 → 0.74 core | — (olay yok) | **FAIL** |
| S13 DB network latency (netem) | checkin, HIGH | checkin p95 7 → 668 ms | checkin etkilendi, mechanism "açıklanamayan" | PASS |
| S17 cascading DB saturation | baggage, HIGH | DB CPU 2.2 → 83.7 % | **db_cpu** / baggage-service | PASS |

Run 1 (aynı senaryolar, Java OneAgent server span'i yokken) de 6/8 idi (`run1-no-server-spans/`); aynı iki case başarısız oldu.

## Bulgular

1. **Katalogdaki mechanism'ler doğru bulunuyor:** row_lock, db_cpu, pool_saturation. Source app her seferinde doğru; S03 high confidence.
2. **S10 — göreli artış kuralı yok.** Connection kullanımı iki katına çıktı (31 → 63 %), ama `db_conn_ratio` mutlak threshold'u 0.80.
   Chaos MEDIUM yoğunluğu threshold'un altında kaldı. Agent "olay yok" dedi; iş latency'si de değişmediği için yanlış alarm yok, ama kaynak baskısı kaçtı.
3. **S11 — container seviyesinde CPU kuralı yok.** `app_vm_cpu` host CPU'ya bakıyor (≥85 % veya +20 puan). baggage container'ı 0.74 core'a
   çıktı, host +11.5 puan. İş latency'si değişmedi.
4. **Network arızası katalog dışında (S13).** Agent etkiyi buldu ve yanlış bir paylaşılan kaynağa (DB/lock/CPU) atfetmedi, ama Root Cause'u
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

## Yeni kurallar (2026-11-24, commit 597e04f)

Kullanıcı onayıyla dört kural eklendi. Eşikler `defaults.yaml` içinde:

| Kural | Koşul |
|---|---|
| `global_connection_pressure` (göreli) | mutlak threshold (0.80) **veya** ratio ≥ 0.60 ve baseline'a göre +0.25 |
| `app_container_cpu` (yeni) | iş uygulaması container'ı baseline'a göre ≥ +0.5 core; host threshold'undan bağımsız |
| `network_path_latency` (yeni) | etkilenen uygulamada idle-in-transaction artışı ≥ 2 **ve** DB exec/call < 1.5× **ve** paylaşılan resource mechanism'i yok; aynı uygulamadaki `pool_saturation` secondary olur |
| Tail latency | p95 değişmese de mean ≥ 5× ve +50 ms ise impact (src `http_mean (tail)`) |

Ek olarak `airlab.pg.sessions` artık `db.role` ile de gruplanıyor. `application_name`'i bilinen bir servis adı olmayan oturumlar
(havuz dışı bağlantılar) role üzerinden uygulamaya bağlanıyor; ham ad features'a girmediği için sızıntı yok. S10'da storm
bağlantıları `<servis>-lab-storm` adıyla geldiği için bu düzeltme olmadan kaynak bulunamıyordu.

Etiketler ve kurallar deneylerden **önce** commit edildi (`597e04f`). Böylece doğrulama etiketlerinin sonuçlardan önce yazıldığı
git geçmişinde de görünüyor.

### Regresyon: ilk setin 8 penceresi (run 2), yeni kurallarla — 7/8

Evidence: `deploy/evidence/chaos-v2-agent-eval/run2-regression-new-rules/`.

| Case | Önce | Sonra |
|---|---|---|
| S03 | row_lock, etkilenen 1/3, puan 35 | row_lock, etkilenen **3/3** (tail), puan 65 |
| S10 | yok (FAIL) | **global_connection_pressure** / baggage, flightops (PASS) |
| S11 | yok (FAIL) | **app_container_cpu** / baggage. Etiket `app_vm_cpu` beklediği için FAIL (etiket, kural yokken yazıldı; değiştirilmedi) |
| S13 | etki var, mechanism yok | **network_path_latency** / checkin |
| Diğerleri | aynı | aynı (C0'da yanlış alarm yok) |

Bu sonuç kuralların türetildiği pencerelerde ölçüldüğü için genelleme evidence'ı **değildir**.

### Doğrulama seti: farklı initiator/intensity — 6/7

Evidence: `deploy/evidence/chaos-v2-agent-validation/`, etiketler `sre-agent/eval/chaos_v2_validation_labels.yaml`.

| Case | Chaos | Agent | Sonuç |
|---|---|---|---|
| V0 | yok | olay yok; yeni kurallarda yanlış alarm yok | PASS |
| V10 | S10 HIGH, reservation + checkin | global_connection_pressure / checkin, reservation | PASS |
| V11 | S11 HIGH, reservation | app_container_cpu / reservation | PASS |
| V13 | S13 HIGH, flightops (DB delay) | pool_saturation / flightops (network bulunamadı) | **FAIL** |
| V14 | S14 HIGH, checkin (packet loss) | network_path_latency / checkin | PASS |
| V03 | S03 HIGH | row_lock / flightops, etkilenen 3/3, puan 81 | PASS |
| V18 | S18 multi-app load | independent_load / reservation; DB/network mechanism'i yok | PASS |

**V13 neden kaçtı:** idle-in-transaction artışı yalnız +1.17 (threshold 2), exec/call ise ×8.5 arttı (0.058 → 0.493 ms). flightops
kendi işlemlerinde sıcak uçuş satırlarını güncelliyor. Network latency'si transaction'ları uzatınca satır lock'ları daha uzun
tutuluyor ve bekleme DB tarafında exec time'a ekleniyor. "Network latency'sinde server-side süre sabit kalır" varsayımı, uygulamanın
kendi içinde contention olduğunda geçerli değil. V14 geçti ama idle-tx artışı 2.11 ile threshold'un hemen üstündeydi.
**`network_path_latency` kuralı kırılgan;** kural doğrulama sonucuna göre yeniden ayarlanmadı. Daha sağlam bir imza için
client-side DB span süresi ile server-side exec süresi farkının (OneAgent DB client span'leri) kullanılması önerilir.

### Kalan sınırlar

- App-to-app network latency (S15/S16) için kural yok. `network_path_latency` yalnız app ↔ PostgreSQL yolunu kapsar.
- S04/S17'de iş endpoint'lerindeki impact (p95 +3–5 ms) agent threshold'unun altında. Chaos controller'ın tepe değerlere duyarlı
  "affected" tanımıyla fark sürüyor.

## Yeniden çalıştırma

```bash
# APP_VM (host uykuya girmemeli; ~90 dk)
python3 scripts/chaos-agent-eval-runs.py [--only S03,S13]
python3 scripts/chaos-agent-eval-runs.py --set validation --out /opt/airlab/deploy/evidence/chaos-v2-agent-validation
# yerel / agent ortamı (DT_ENV, DT_PLATFORM_TOKEN), windows.yaml'ı sre-agent/eval/chaos_v2_windows.yaml olarak kopyalayın
python eval/run_eval.py --real --windows chaos_v2_windows.yaml --labels chaos_v2_labels.yaml
python eval/run_eval.py --real --windows chaos_v2_validation_windows.yaml --labels chaos_v2_validation_labels.yaml
```

### PostgreSQL yük seti (S01–S19)

Senaryo seti 2026-11-27'de yalnız PostgreSQL yükü üretecek biçimde yenilendi; yukarıdaki S kimliklerinin bir kısmı artık
başka yük tiplerini gösterir. Güncel set için koşu tanımları `--set db` (20 case: `D0` kontrol + `D01`–`D19`), etiketler
`sre-agent/eval/chaos_db_labels.yaml` dosyasındadır. Etiketler set koşulmadan yazıldı; ancak yük tiplerinin çoğu agent'ın
mechanism eşikleri seçilirken farklı initiator / intensity ile ölçüldüğü için bu set kör değil, doğrulama setidir.

```bash
# APP_VM (host uykuya girmemeli; ~4 saat)
python3 scripts/chaos-agent-eval-runs.py --set db --out /opt/airlab/deploy/evidence/chaos-db-agent-eval
# windows.yaml'ı sre-agent/eval/chaos_db_windows.yaml olarak kopyalayın
python eval/run_eval.py --real --windows chaos_db_windows.yaml --labels chaos_db_labels.yaml
```

# Deney reçeteleri

Reçeteler `lab/chaos-controller/src/main/resources/presets/*.json` içindedir; panelden "Hazır reçete" ile veya
`scripts/run-experiment.py <reçete>` ile çalıştırılır. Tüm parametreler panelde düzenlenebilir.

## Ortak kurallar

- **Pencereler** (varsayılan): warm-up 30 sn → baseline 120 sn → yük 180 sn → recovery 120 sn. Her fazın UTC
  başlangıç/bitişi deney kaydına yazılır; Dynatrace sorguları bu pencerelerle kurulur. Warm-up analiz dışıdır.
- **Normal arka plan trafiği**: dört uygulamaya tüm fazlarda 1 RPS/uygulama normal karışım
  (Reservation: arama 50 / okuma 25 / oluşturma 15 / iptal 10; Check-in: sorgu 40 / check-in 30 / koltuk 20 / harita 10;
  FlightOps: liste 40 / detay 40 / durum 15 / kapasite 5; Baggage: durum 45 / uçuş bagajları 15 / kayıt 25 / olay 15).
- **Stres** hedefleri yalnız yük fazında; **probe** hedefleri (activePhases=ALL) tüm fazlarda düşük hızla etkiyi ölçer.
- **RPS** (açık döngü) ile **CONCURRENCY** (kapalı döngü) bilinçli olarak ayrıdır; RPS modunda maxInFlight dolunca
  istek kuyruğa alınmaz, `dropped` sayılır.
- **Tekrar**: nedensellik iddiası için her deney en az 3 kez, arada ≥ 2 dk recovery ile çalıştırılmalı; ayrıca
  E1 (etkisiz) ile karşılaştırılmalı. Tek koşu korelasyondur.
- **Veri ölçeği**: varsayılan `seed --scale=1` (≈204 uçuş, 28k rezervasyon, 33k bag_event, 33 MB).
  E6 için `seed --scale=2 --events-per-bag=60` (≈800k bag_event, 344 MB) veya hedef DB_VM'de
  shared_buffers'ı (1 GB) aşacak şekilde `--scale=10 --events-per-bag=100` (≈2 GB, önce disk alanını kontrol edin).
- Sayılar **donanıma bağlıdır**. Aşağıdaki "yerel ölçüm" sütunu yalnız geliştirme makinesindeki tek koşudur
  (Docker Desktop, 12 vCPU, PG container 1 GB, shared_buffers 256 MB, kısa pencereler). Hedef VM'lerde yeniden ölçülmelidir.

## Reçeteler

| ID | Kaynak (stres) | Etkilenmesi beklenen | Mekanizma | Beklenen kanıt (Dynatrace + kayıt) |
|---|---|---|---|---|
| **E0** normal-baseline | yok | yok | — | Tüm servislerde kararlı p95/throughput; `lock.waiters_total=0`; referans pencere |
| **E1** independent-baggage-read | Baggage 40 RPS (`bag_status`, `flight_bags`) | **hiçbiri** | Baggage okumaları kilitsiz, indeksli; ortak satıra dokunmaz | Baggage throughput ↑; diğer üç servis p95/hata değişmez; blocking kenarı yok. **Yanlış pozitif kontrolü** |
| **E2** hot-row-lock | Reservation CONC 4, `lab_hold_booking` holdMs=2000 (sıcak uçuş) | Check-in `checkin` (probe 3 RPS, aynı sıcak uçuş) | Reservation `UPDATE flight_inventory SET sold+1` → satır kilidi commit'e kadar; Check-in `checked_in+1` aynı satırı bekler | Check-in `checkin` p95 ↑, `blocking_edge` waiter=checkin-service/blocker=reservation-service, `wait_event=transactionid`, `blocker.lab_hold=hold-inventory-lock`; Check-in koltuk/okuma etkilenmez (veya yalnız pool kuyruğu kadar) |
| **E2b** reverse | Check-in CONC 4, `lab_hold_checkin` | Reservation `create_booking` (probe, sıcak uçuş) | Ters yön aynı satır | blocker=checkin-service |
| **E3** single-pool-saturation | Reservation CONC 16, `lab_hold_connection` (main pool) holdMs=1500 | Reservation'ın kendi tüm operasyonları | Hikari pool (8) dolar, threads pending | `hikaricp.connections.pending{pool=reservation-pool}` ↑, acquire ↑, `POOL_TIMEOUT`; DB toplam bağlantı **değişmez**; diğer servisler etkilenmez |
| **E4** global-connection-pressure | 4 uygulama × CONC 22, `lab_pressure_connection` holdMs=4000 | Yeni bağlantı açmak zorunda kalan her havuz | Ayrı `*-lab-pressure` havuzları DB'nin normal slotlarını (94) doldurur | `airlab.pg.connections.used_ratio_normal` ≈ 1, PG log `remaining connection slots are reserved`, pressure havuzlarında acquire ↑; mevcut bağlantısı olan ana havuzlar etkilenmeyebilir (bkz. bulgu) |
| **E5** db-cpu | FlightOps CONC 6–8, `load_factor_report` dataSize=30 | Aynı DB'yi kullanan diğer servisler (DB CPU yeterince doyarsa) | Gerçek join+agregasyon sorgusu | DB_VM CPU ↑, `airlab.pg.statements.exec_time_ms{db.role=svc_flightops}` ↑, top_statement; APP_VM CPU değişimi ayrı |
| **E6** io | Baggage CONC 4, `bag_history_report` dataSize=12 | Okuma ağırlıklı diğer sorgular (cache tahliyesi) | bag_event tam tarama (flight_id indeksi yok) | `postgres.blks_read.count` ↑, `airlab.pg.statements.shared_blks_read{svc_baggage}` ↑, host disk okuma ↑ |
| **E7** two-app | Reservation 40 RPS (rw=0.5) + FlightOps CONC 4 rapor | — | İki kaynak eşzamanlı | İki kaynağın etkisi ayrı ayrı görülmeli; "tek suçlu" yerine "birden fazla kaynak" |
| **E8** four-app + recovery | 4 × 30 RPS normal karışım | — | Genel yük, sonra toparlanma | Recovery'de tüm servislerin baseline'a dönmesi; dönmeyen varsa ayrı kanıt |

## Yerel ölçüm sonuçları (tek koşu, kısa pencereler) — `YEREL-DOĞRULANDI`

| Deney | Pencere (sn) | Sonuç |
|---|---|---|
| E2 | 10/40/50/30 | Check-in probe `checkin` p50 **25 → 786 ms**, p95 **34 → 3 867 ms**, p99 7,5 s; recovery p50 22 ms (p95 963 ms, yük kesildiği ilk saniyelerin kuyruğu). Collector: 50 blocking kenarı, en sık `checkin-service/checkin ← reservation-service/lab_hold_booking (hold-inventory-lock)`; tümü `experiment_id` ile eşleşti. Baggage/FlightOps arka planı değişmedi. Check-in'in kilitsiz `lookup_booking` p95'i de yükseldi (31 → 424 ms): bekleyen check-in'ler Check-in'in kendi Hikari havuzunu (8) işgal etti → **ikincil mekanizma: kilit beklemesi → kurban servisin havuz doygunluğu** |
| E1 | 5/40/45/30 | Baggage hedefi ~35 RPS (1 600 istek, p95 7 ms). Dört servisin arka planı: 0 hata, op-bazlı en yüksek p95 baseline/yük/recovery 36/35/36, 37/33/32, 31/28/29, 39/34/33 ms. Etki **yok** (doğru negatif) |
| E3 | 5/40/45/30 | Reservation arka planı p50 6–27 ms → **1,4–3,1 s**, hedefte 9 adet 5xx `POOL_TIMEOUT`; Check-in/FlightOps/Baggage değişmedi; DB bağlantı sayısı artmadı |
| E4 | 5/30/90(stop @75)/30 | DB 95/100 bağlantı (`*-lab-pressure` 18–19/servis). Yükün ortasında stop-all → 4 servis cleanup 39 backend iptal etti, pressure havuzları kapandı, aktif `lab:` sorgusu **0**. 2. koşuda PG 39 bağlantıyı `remaining connection slots are reserved` ile reddetti; Hikari yeniden denediği için hata yerine pressure acquire gecikmesi (p95 ≈ hold + 850 ms) oluştu. Ana havuzlar mevcut bağlantılarını koruduğundan arka plan etkilenmedi → **kalibrasyon bulgusu**: hata üretmek için concurrency/hold artırılmalı veya ana havuz bağlantı yenilemesi (maxLifetime) beklenmeli |
| E5 | 5/20/40/20 | PG container CPU **%1,6 → %634**; `load_factor_report` 201 RPS, p95 58 ms; top_statement: svc_flightops queryid `-4120088193024117288` 8 051 çağrı, 208 s toplam |
| E6 | seed scale=2, epb=60; 5/20/40/20 | bag_event 313 MB > shared_buffers 256 MB. `blks_read` +199 728 blok; collector `statements.shared_blks_read{svc_baggage}` delta 140 148; rapor p50 852 ms. Disk düzeyi I/O ayrıca ölçülmedi (Docker Desktop page cache) |
| E7 | 5/25/45/30 | 2 hedef birlikte; FlightOps 216 RPS, Reservation 31 RPS; arka plan hatasız |
| E8 | 5/25/45/30 | 4 hedef × 20 RPS gerçekleşti (hedef 30, ramp-up 30 sn içinde); arka plan hatasız; 409'lar iş kuralı (iptal-sonrası check-in, bagaj limiti) |

Üretici (chaos-controller, 1 vCPU limit) tüketimi: ortalama CPU %9–23, maksimum %77 (E7), heap ≤ 68 MB.

## Çalıştırma

```bash
export CHAOS_URL=http://<APP_VM>:8080 CHAOS_USER=chaos CHAOS_PASSWORD=...
python3 scripts/run-experiment.py E2-hot-row-lock                         # reçete pencereleriyle
python3 scripts/run-experiment.py E2-hot-row-lock --baseline 60 --load 90 --set reservation.holdMs=1500
python3 scripts/run-experiment.py E7-two-app-concurrent --targets reservation,flightops
python3 scripts/run-experiment.py E4-global-connection-pressure --stop-after 120   # durdurma testi
```

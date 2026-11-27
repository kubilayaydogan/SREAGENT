# airlab SRE raporu `rep-20261121-174115-e33bd6`
- Tür: analyze · Durum: **complete** · Üretim: 2026-11-21T17:41:15Z
- Pencere `baseline`: 2026-11-21T14:47:00Z → 2026-11-21T14:51:00Z
- Pencere `window`: 2026-11-21T14:52:00Z → 2026-11-21T14:56:00Z
- Not: Baseline kullanıcı tarafından verildi.

## Üç ayrı değerlendirme
| Boyut | Değer |
|---|---|
| Risk şiddeti (0–100) | 65.1 (yüksek) |
| Veri kapsamı | 15/15 sinyal OK, puan kapsamı ort. 100.0% |
| Nedensellik güveni | yüksek (row_lock) |

## Uygulama puanları
| Uygulama | Puan | Bant | Kapsam | Gecikme oranı | Eksik bileşen |
|---|---|---|---|---|---|
| reservation-service | 15.0 | düşük | 100.0% | ×0.78 (span_p95) | — |
| checkin-service | 65.1 | yüksek | 100.0% | ×131.80 (span_p95) | — |
| flightops-service | 0.0 | düşük | 100.0% | ×1.00 (span_p95) | — |
| baggage-service | 0.0 | düşük | 100.0% | ×0.96 (span_p95) | — |

## Kaynak → Etkilenen
- Kaynak: reservation-service
- Etkilenen: checkin-service
  - checkin-service: gecikme ×131.8 (span_p95, +3924 ms); havuz zaman aşımı 1

## Mekanizmalar
- **Satır kilidi beklemesi** (`row_lock`, güven: yüksek)
  - Kaynak: reservation-service · Etkilenen: checkin-service
  - Kaynak uygulamanın açık transaction'ı satır kilidini tutarken etkilenen uygulamanın UPDATE/SELECT FOR UPDATE işlemleri Lock/transactionid beklemesine düşüyor; bekleyen istekler Hikari bağlantısını tuttuğu için havuz doluluğu ve gecikme artıyor.
  - Sinyaller: blocking_edge logları, pg lock waiters metriği, PostgreSQL log_lock_waits · Kanıt: ev-3da6d874cc, ev-1f65c0cea3, ev-6dab0cfb9b, ev-97fa45257d
- **checkin-service bağlantı havuzu doygunluğu** (`pool_saturation`, güven: orta, ikincil → row_lock)
  - Kaynak: reservation-service · Etkilenen: checkin-service
  - Kilit beklemesinin sonucu: bekleyen istekler bağlantıları tutuyor.
  - Sinyaller: Hikari aktif/max · Kanıt: ev-9d19b96fae, ev-0526fc3b03

## Alternatif açıklamalar
- —

## Veri boşlukları
- Yok

## Önerilen insan incelemesi
- Kaynak uygulamanın uzun süren/boşta açık transaction'larını (blocker_state, xact_age) inceleyin; pg_stat_activity'de 'idle in transaction' oturumları ve uygulama transaction sınırlarını doğrulayın.

## Açıklama (Claude (claude-sonnet-5-5))
## Özet
- **Risk puanı:** 65,1 (**yüksek**), yalnızca checkin-service kaynaklı. reservation-service 15,0 (düşük), flightops ve baggage 0,0.
- **Veri kapsamı:** 1,0 (15/15 sinyal, 4/4 uygulama puanlandı).
- **Nedensellik güveni:** `row_lock` için yüksek, `pool_saturation` için orta (ikincil).
- checkin-service p95 gecikmesi 30 ms'den 3954 ms'ye çıktı (×131,8, span_p95). Havuz zaman aşımı 1, hata oranı 0,005.

Bu dört kavram ayrıdır. Yüksek bir risk bandı nedensellik kesinliği anlamına gelmez.

## Kaynak → Etkilenen
- **Kaynak:** reservation-service, `lab_hold_booking` işlemi (ev-3da6d874cc, ev-1f65c0cea3, ev-6dab0cfb9b, ev-97fa45257d).
- **Etkilenen:** checkin-service, `checkin` işlemi. Bekleme türü Lock; örneklerde `transactionid` ve `tuple` (ev-20feee3507).
- Toplam 339 blocking edge ve 1117 `log_lock_waits` kaydı var. Baseline'da ikisi de 0.

## Mekanizma
- Kaynağın açık transaction'ı satır kilidini tutuyor, checkin güncellemeleri bekliyor. Bekleyen istekler Hikari bağlantısını tuttuğundan havuz doluyor (en yüksek 0,875, ortalama 0,625; ev-9d19b96fae, ev-0526fc3b03).
- DB lock waiter ortalaması 6, en yüksek 20. checkin-service için ortalama 11.
- En büyük çift: `lab_hold_booking` → `checkin`, blocker bekleme olayı `Timeout`, n=143, en uzun bekleme 9,78 sn.
- Örnek kayıt: bekleyen 8,80 sn, blocker xact yaşı 4,92 sn (ev-20feee3507).

## Alternatifler
Motor alternatif açıklama üretmedi (`alternatives` boş). Yine de veride şunlar var:
- **Karşılıklı ve kendi içinde bekleme:** `checkin` → `checkin` (n=62, en uzun 5,79 sn) ve `checkin` → `lab_hold_booking` (n=12, en uzun 2,89 sn). Zincir blocker'ı olarak checkin-service de işaretli. Tek yönlü "reservation → checkin" anlatımı eksik kalabilir.
- `lab_hold_booking` içi çekişme (n=69) kaynağın kendi kendini de yavaşlatabildiğini gösteriyor.
- Host CPU, iowait, bellek ve GC belirgin değişmedi, yani kaynak yetersizliği kanıtı yok.

## Veri boşlukları
- Motor boşluk bildirmedi (`gaps` ve `data_gaps` boş).
- Veride sorgu metni, transaction sınırları ve kilidin neden tutulduğu yok.
- Pencereler 4'er dakika. Baseline kullanıcı tarafından verildi.
- `chaos-controller` konteyneri görünüyor ama olay verisi yok. İlişkisi hakkında yorum yapılamaz.
- `rpm_ratio` boş (null).

## Önerilen insan incelemesi
- reservation-service'in `lab_hold_booking` transaction'larını (blocker_state, xact_age) ve `pg_stat_activity`'de "idle in transaction" oturumlarını inceleyin. `Timeout` bekleme olayının nedenini doğrulayın.
- checkin-service içindeki `checkin` ↔ `checkin` tuple kilidi çekişmesini ayrıca değerlendirin.
- Bu bulgular korelasyon ve edge kanıtına dayanır, kesin kök neden hükmü değildir.

## Kanıtlar
| ID | Sinyal | Durum | Kayıt | Dynatrace queryId |
|---|---|---|---|---|
| ev-0526fc3b03 | http | OK | 8 | 01a1ff32-c821-7bfe-a861-cce72369beb5 |
| ev-d1531e8797 | spans | OK | 10 | 01a1ff32-c86f-744e-8bd6-5b5177deb799 |
| ev-9d19b96fae | hikari | OK | 4 | 01a1ff32-c81d-7dcd-958a-1583a074fa05 |
| ev-ca5ba92a42 | jvm_gc | OK | 4 | 01a1ff32-c901-7233-8cb4-a43b7ffc4f4a |
| ev-4db2cfc9a8 | jvm_heap | OK | 4 | 01a1ff32-cde2-7994-8046-1d1c481263b7 |
| ev-074b644512 | hosts | OK | 2 | 01a1ff32-cea4-76ce-82b6-cfd6e21d6bbc |
| ev-7a5d3c3c45 | containers | OK | 6 | 01a1ff32-d0d9-7bd8-868d-3a046827b269 |
| ev-25ce79e17e | db_conn | OK | 1 | 01a1ff32-d0fd-7775-b816-e791c83348a9 |
| ev-eeed0339b1 | db_sessions | OK | 11 | 01a1ff32-d1fa-7ff4-a624-8e1a8f406203 |
| ev-6dab0cfb9b | lock_waiters | OK | 4 | 01a1ff32-d383-78f7-9863-bf825db00aae |
| ev-1f65c0cea3 | collector | OK | 1 | 01a1ff32-d3c0-78f6-80a4-59d6d2f2b4f4 |
| ev-3da6d874cc | edges | OK | 11 | 01a1ff32-d478-7b5f-8bee-215f1ff4ee1e |
| ev-97fa45257d | pg_lock_logs | OK | 1 | 01a1ff32-d4da-711f-abb8-7f4ff755c00e |
| ev-5e52ebc0c3 | db_stats | OK | 1 | 01a1ff32-d684-7652-97c2-22ffdff509c7 |
| ev-add6e8c858 | statements | OK | 7 | 01a1ff32-d777-7980-ac0f-737d06d61862 |
| ev-20feee3507 | edges_detail | OK | 15 | 01a1ff32-e7fc-7002-8ed3-497115daf22f |

_Bütçe: 16 sorgu, LLM 1 çağrı / 9771+2382 token, süre 34.8 sn._

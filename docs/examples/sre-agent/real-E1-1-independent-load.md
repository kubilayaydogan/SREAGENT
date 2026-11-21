# airlab SRE raporu `rep-20261121-174154-8d495c`
- Tür: analyze · Durum: **complete** · Üretim: 2026-11-21T17:41:54Z
- Pencere `baseline`: 2026-11-21T15:02:00Z → 2026-11-21T15:06:00Z
- Pencere `window`: 2026-11-21T15:07:00Z → 2026-11-21T15:11:00Z
- Not: Baseline kullanıcı tarafından verildi.

## Üç ayrı değerlendirme
| Boyut | Değer |
|---|---|
| Risk şiddeti (0–100) | 0.0 (düşük) |
| Veri kapsamı | 15/15 sinyal OK, puan kapsamı ort. 100.0% |
| Nedensellik güveni | orta (independent_load) |

## Uygulama puanları
| Uygulama | Puan | Bant | Kapsam | Gecikme oranı | Eksik bileşen |
|---|---|---|---|---|---|
| reservation-service | 0.0 | düşük | 100.0% | ×0.76 (span_p95) | — |
| checkin-service | 0.0 | düşük | 100.0% | ×0.93 (span_p95) | — |
| flightops-service | 0.0 | düşük | 100.0% | ×0.78 (span_p95) | — |
| baggage-service | 0.0 | düşük | 100.0% | ×0.17 (span_p95) | — |

## Kaynak → Etkilenen
- Kaynak: baggage-service
- Etkilenen: yok

## Mekanizmalar
- **baggage-service bağımsız yük artışı** (`independent_load`, güven: orta)
  - Kaynak: baggage-service · Etkilenen: —
  - İstek hızı arttı; paylaşılan kaynakta (kilit, DB bağlantısı, CPU) baskı ölçülmedi. Diğer uygulamalara yayılma gözlenmedi.
  - Sinyaller: Micrometer istek hızı · Kanıt: ev-675253da05

## Alternatif açıklamalar
- —

## Veri boşlukları
- Yok

## Önerilen insan incelemesi
- Belirgin bir risk yok; rutin izleme yeterli.

## Açıklama (Claude (claude-sonnet-5-5))
# Analiz Raporu

## Özet
- **Risk puanı:** 0.0 — **şiddet bandı:** düşük (4/4 uygulama puanlandı).
- **Veri kapsamı:** 1.0 (15/15 sinyal; boşluk yok).
- **Nedensellik güveni:** orta (`independent_load`).
- Pencere (15:07–15:11Z) baseline'a (15:02–15:06Z) göre karşılaştırıldı. Baseline kullanıcı tarafından verildi.
- Bu üç kavram ayrıdır: risk düşük olsa da yük artışı gerçektir, yalnızca zarar ölçülmemiştir.

## Kaynak → Etkilenen
- **Kaynak:** baggage-service. İstek hızı 65,75 → 2228 rpm (≈33,89×) (ev-675253da05).
- **Etkilenen:** yok. Diğer uygulamalarda yayılma gözlenmedi.
  - checkin-service: 63,25 → 63 rpm
  - flightops-service: 55 → 63 rpm
  - reservation-service: 69 → 68 rpm

## Mekanizma
- Mekanizma: bağımsız yük artışı (ev-675253da05). Paylaşılan kaynakta (kilit, DB bağlantısı, CPU) baskı ölçülmedi.
- baggage-service'te span p95 30 → 5 ms, hata oranı 0, pool_pending 0, pool_timeouts 0.
- DB tarafı: bağlantı oranı 0,181 (17/94) değişmedi; kilit bekleyeni, deadlock ve lock-wait logu yok.
- Host CPU artışı sınırlı: app-vm 4,89 → 7,59, db-vm 0,62 → 1,32.
- baggage-service konteyner CPU'su 0,0147 → 0,1131 çekirdek.
- Güven "orta"dır; bu bir korelasyon bulgusudur, kesin nedensellik değildir.

## Alternatifler
- Analiz motoru alternatif açıklama üretmedi (`alternatives` boş).
- Yorum olarak: gecikmenin düşmesi yük tipinin (ör. çok hafif veya önbelleğe alınabilir istekler) değiştiğini düşündürebilir. Bu veriyle doğrulanamaz ve kanıt kimliği yoktur.

## Veri boşlukları
- Raporlanan bir boşluk yok (`gaps` boş, `data_gaps` boş).
- Pencere yalnızca 4 dakikalık; kısa süreli etkiler veya uzun vadeli birikim görünmeyebilir.
- Yük artışının kaynağı (istemci, test, iş akışı) veride yok.
- chaos-controller konteynerinde CPU artışı var (0,0756 → 0,1093 çekirdek). Bu veriyle ilişkisi belirlenemez.

## Önerilen insan incelemesi
- Belirgin risk yok; rutin izleme yeterli.
- İsteğe bağlı: 33× yükün planlı bir test veya bilinen bir trafik olup olmadığını doğrulayın.
- İsteğe bağlı: baggage-service'in gecikme düşüşünün yük tipi değişikliğinden kaynaklanıp kaynaklanmadığını kontrol edin.
- Yük sürerse baggage-service havuz oranı (0,125) ve host CPU'sunu izleyin.

## Kanıtlar
| ID | Sinyal | Durum | Kayıt | Dynatrace queryId |
|---|---|---|---|---|
| ev-675253da05 | http | OK | 5 | 01a1ff33-5d6b-7641-9bf0-5b792d15ce84 |
| ev-ff17a60f67 | spans | OK | 10 | 01a1ff33-5f27-7d55-9e4d-27e153950081 |
| ev-636027f5bc | hikari | OK | 4 | 01a1ff33-5d94-7278-933e-a65f464705a0 |
| ev-a47bce5f43 | jvm_gc | OK | 4 | 01a1ff33-5f29-73ad-885c-8d9ee8cc9e0d |
| ev-5aab7c7174 | jvm_heap | OK | 4 | 01a1ff33-63ed-7c87-843f-7fea1833323c |
| ev-978a574b5c | hosts | OK | 2 | 01a1ff33-6593-7401-8185-0b4ac2c1753d |
| ev-3aa81d0cd1 | containers | OK | 6 | 01a1ff33-667d-7864-9adc-f753b13ff8fc |
| ev-c13adaabdb | db_conn | OK | 1 | 01a1ff33-6870-7687-9b5c-8663796c6452 |
| ev-85456a3eb6 | db_sessions | OK | 5 | 01a1ff33-695e-75dc-a1e3-1fcb9642e355 |
| ev-11d76c872c | lock_waiters | EMPTY | 0 | 01a1ff33-6b3a-7637-a60a-4b874905e206 |
| ev-33e857ae8d | collector | OK | 1 | 01a1ff33-6c23-739d-a7ca-092c47c86bad |
| ev-024347d553 | edges | EMPTY | 0 | 01a1ff33-6dc4-7add-a51c-d831f65187f6 |
| ev-29f7a80192 | pg_lock_logs | EMPTY | 0 | 01a1ff33-6e01-737d-afd8-3f8c6840f4e7 |
| ev-de6a365b82 | db_stats | OK | 1 | 01a1ff33-6eef-7115-ad2f-7ad6ce21954f |
| ev-25f134399b | statements | OK | 7 | 01a1ff33-7028-7639-97f4-4af9553105ba |

_Bütçe: 15 sorgu, LLM 1 çağrı / 4216+1221 token, süre 20.7 sn._

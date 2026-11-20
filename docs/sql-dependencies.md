# Tablo / transaction / kilit bağımlılıkları

Tek PostgreSQL instance, tek database `airlab`, şema = sahiplik sınırı. Ortak sıcak kaynak:
**`flightops.flight_inventory`** (uçuş başına tek satır: `capacity`, `sold`, `checked_in`, `version`).

## Şemalar ve yetkiler (V3__grants.sql)

| Rol | Yazar | Okur | Ortak satırda değiştirebildiği kolon |
|---|---|---|---|
| svc_reservation | reservation.* | flightops.flight/airport/inventory, checkin.checkin | `sold, version, updated_at` |
| svc_checkin | checkin.* | reservation.booking/passenger, flightops.flight/inventory | `checked_in, version, updated_at` (+ booking.updated_at: yalnız FOR SHARE için) |
| svc_flightops | flightops.* | reservation.booking | tüm envanter kolonları (`capacity`) |
| svc_baggage | baggage.* | reservation.booking, checkin.checkin, flightops.flight | **yok** |

Rol ayarları: `statement_timeout=15s`, `lock_timeout=8s`, `idle_in_transaction_session_timeout=30s`, `CONNECTION LIMIT 40`.

## İşlem yolları

| Servis.operasyon | Transaction adımları (sırayla) | Alınan kilitler | Kimi bekler / kimi bekletir |
|---|---|---|---|
| Reservation.create_booking | SELECT flight (kilitsiz) → INSERT passenger → **UPDATE inventory sold+1 WHERE sold<capacity** → INSERT booking (FK→flight: FOR KEY SHARE) → [lab: pg_sleep] → COMMIT | inventory satırı FOR NO KEY UPDATE; flight satırı KEY SHARE | Check-in.checkin, FlightOps.update_capacity, diğer create/cancel ile **aynı uçuşta** serileşir |
| Reservation.cancel_booking | SELECT booking FOR UPDATE → EXISTS checkin? → UPDATE booking → **UPDATE inventory sold-1** | booking satırı FOR UPDATE; inventory | Check-in.checkin (booking FOR SHARE) ile serileşir |
| Reservation.search / get / route_demand | SELECT (MVCC) | yok | kilit beklemez; route_demand booking'i tam tarar (CPU) |
| Check-in.checkin | SELECT booking FOR SHARE → INSERT checkin ON CONFLICT DO NOTHING → **UPDATE inventory checked_in+1** → [lab hold] | booking FOR SHARE; checkin unique index; inventory | **E2'de etkilenen operasyon**: Reservation/FlightOps aynı satırı tutuyorsa bekler (`Lock/transactionid`) |
| Check-in.assign_seat | SELECT checkin (kilitsiz) → SELECT seat FOR UPDATE → INSERT/UPDATE seat (PK flight_id+seat_no) | yalnız checkin.seat_assignment | envantere dokunmaz |
| Check-in.lookup | SELECT (MVCC) | yok | |
| FlightOps.update_status | SELECT flight FOR NO KEY UPDATE → UPDATE flight → INSERT ops_event | flight satırı (NO KEY UPDATE, FK'nin KEY SHARE'i ile **çakışmaz**) | satışları bloklamaz |
| FlightOps.update_capacity | **UPDATE inventory capacity WHERE sold<=:cap** → INSERT ops_event → [lab hold] | inventory | Reservation/Check-in ile serileşir |
| FlightOps.load_factor | flight × inventory × booking join + GROUP BY (MVCC) | yok | DB CPU |
| Baggage.register_bag | SELECT booking + EXISTS checkin (kilitsiz, **FK yok**) → pg_advisory_xact_lock(booking) → COUNT bags → INSERT bag + bag_event | yalnız baggage içi advisory kilit | ortak kaynaktan bağımsız (**kontrol grubu**) |
| Baggage.bag_event | SELECT bag FOR UPDATE → UPDATE bag → INSERT bag_event | bag satırı | |
| Baggage.bag_history | bag_event tam tarama (flight_id indeksi yok) | yok | I/O |

## Veri bütünlüğü kuralları

| Kural | Mekanizma | Test |
|---|---|---|
| Aşırı satış yok | koşullu UPDATE `sold<capacity` + `CHECK (sold<=capacity)` | ReservationIT: 10 eşzamanlı istek, kapasite 3 → tam 3 başarılı |
| Tekrar gönderim veri bozmaz | `Idempotency-Key` UNIQUE (booking, bag); checkin `UNIQUE(booking_id)` + ON CONFLICT; koltuk/durum aynıysa replay | ReservationIT, CheckinIT (5 eşzamanlı check-in → 1 kayıt), BaggageIT, smoke |
| Check-in sonrası iptal yok | cancel FOR UPDATE ↔ checkin FOR SHARE serileşmesi + EXISTS kontrolü | smoke §3 |
| İptal edilmiş rezervasyona check-in yok | FOR SHARE sonrası durum kontrolü | CheckinIT |
| Koltuk tekil | PK(flight_id, seat_no) + UNIQUE(booking_id) | CheckinIT |
| Kapasite < satılan olamaz | koşullu UPDATE + CHECK | FlightOpsIT, smoke §6 |
| `sold`/`checked_in` sayaç tutarlılığı | `lab.integrity_check` görünümü | tüm IT'ler, smoke §7, `db-migrator check` |

## Bilinçli tasarım notları
- PostgreSQL'de sıradan SELECT satır kilidini beklemez; bu yüzden kilit deneyinde etkilenen operasyon **aynı satırı
  güncelleyen** gerçek iş yoludur (check-in), okuma değildir.
- Baggage'ın booking'e FK'si yoktur: FK kontrolü booking satırında FOR KEY SHARE alır ve iptalin FOR UPDATE'i ile
  çakışırdı; bağımsız yolun bağımsız kalması için doğrulama kilitsiz SELECT ile yapılır (tutarlılık bedeli: iptal
  ile eşzamanlı bagaj kaydı nadiren iptal edilmiş rezervasyona düşebilir; lab'da kabul edildi ve belgelendi).
- `pg_sleep` yalnız lab uçlarında, sınırlı (`LAB_MAX_HOLD_MS`, varsayılan 5 sn) ve `/* lab:... */` işaretli kullanılır;
  CPU/I/O yükü için değil.

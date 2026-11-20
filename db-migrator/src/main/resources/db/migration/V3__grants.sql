-- En az yetki. Roller DB_VM'de db-vm/create-roles.sql ile (superuser) önceden oluşturulur.
-- Kolon düzeyi UPDATE yetkileri, hangi servisin ortak satırın hangi alanını
-- değiştirebildiğini belgeler.

-- ---------- svc_flightops ----------
GRANT USAGE ON SCHEMA flightops, reservation TO svc_flightops;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA flightops TO svc_flightops;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA flightops TO svc_flightops;
GRANT SELECT ON reservation.booking TO svc_flightops;               -- load-factor raporu

-- ---------- svc_reservation ----------
GRANT USAGE ON SCHEMA reservation, flightops, checkin TO svc_reservation;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA reservation TO svc_reservation;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA reservation TO svc_reservation;
GRANT SELECT ON flightops.airport, flightops.flight, flightops.flight_inventory TO svc_reservation;
GRANT UPDATE (sold, version, updated_at) ON flightops.flight_inventory TO svc_reservation;
GRANT SELECT ON checkin.checkin TO svc_reservation;                  -- check-in sonrası iptal kuralı

-- ---------- svc_checkin ----------
GRANT USAGE ON SCHEMA checkin, reservation, flightops TO svc_checkin;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA checkin TO svc_checkin;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA checkin TO svc_checkin;
GRANT SELECT ON reservation.booking, reservation.passenger TO svc_checkin;
-- SELECT ... FOR SHARE en az bir kolonda UPDATE yetkisi ister (PostgreSQL kuralı).
-- Check-in booking satırını FOR SHARE ile kilitler ki eşzamanlı iptal ile yarışmasın.
GRANT UPDATE (updated_at) ON reservation.booking TO svc_checkin;
GRANT SELECT ON flightops.flight, flightops.flight_inventory TO svc_checkin;
GRANT UPDATE (checked_in, version, updated_at) ON flightops.flight_inventory TO svc_checkin;

-- ---------- svc_baggage ----------
GRANT USAGE ON SCHEMA baggage, reservation, checkin, flightops TO svc_baggage;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA baggage TO svc_baggage;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA baggage TO svc_baggage;
GRANT SELECT ON reservation.booking, checkin.checkin, flightops.flight TO svc_baggage;

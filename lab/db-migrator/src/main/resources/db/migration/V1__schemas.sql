-- Tek migration runner (db-migrator) bu dosyaları lab_owner rolüyle çalıştırır.
-- Uygulama servisleri migration çalıştırmaz (spring.flyway yok).
-- Şema = sahiplik sınırı. Ortak sıcak kaynak: flightops.flight_inventory.
CREATE SCHEMA IF NOT EXISTS flightops;
CREATE SCHEMA IF NOT EXISTS reservation;
CREATE SCHEMA IF NOT EXISTS checkin;
CREATE SCHEMA IF NOT EXISTS baggage;
CREATE SCHEMA IF NOT EXISTS lab;

-- Varsayılan PUBLIC yetkilerini kapat (PG15+ zaten public şemasında CREATE vermez).
REVOKE ALL ON SCHEMA flightops, reservation, checkin, baggage, lab FROM PUBLIC;

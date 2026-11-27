-- =========================================================================
-- FLIGHT OPERATIONS (sahip: svc_flightops)
-- =========================================================================
CREATE TABLE flightops.airport (
    code        char(3) PRIMARY KEY,
    city        varchar(64) NOT NULL,
    country     varchar(64) NOT NULL
);

CREATE TABLE flightops.flight (
    flight_id    bigserial PRIMARY KEY,
    flight_no    varchar(8)  NOT NULL,
    origin       char(3)     NOT NULL REFERENCES flightops.airport(code),
    destination  char(3)     NOT NULL REFERENCES flightops.airport(code),
    dep_time     timestamptz NOT NULL,
    arr_time     timestamptz NOT NULL,
    aircraft     varchar(16) NOT NULL,
    status       varchar(16) NOT NULL DEFAULT 'SCHEDULED'
                 CHECK (status IN ('SCHEDULED','DELAYED','BOARDING','DEPARTED','ARRIVED','CANCELLED')),
    gate         varchar(8),
    delay_min    integer     NOT NULL DEFAULT 0 CHECK (delay_min >= 0),
    is_hot       boolean     NOT NULL DEFAULT false,   -- kaos deneyleri için işaretli "sıcak" uçuşlar
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT flight_route_chk CHECK (origin <> destination),
    CONSTRAINT flight_time_chk  CHECK (arr_time > dep_time),
    CONSTRAINT flight_no_dep_uq UNIQUE (flight_no, dep_time)
);
CREATE INDEX flight_route_dep_idx ON flightops.flight (origin, destination, dep_time);
CREATE INDEX flight_dep_idx       ON flightops.flight (dep_time);
CREATE INDEX flight_hot_idx       ON flightops.flight (flight_id) WHERE is_hot;

-- ORTAK SICAK KAYNAK: uçuş başına tek satır.
--   Reservation: sold +/- 1      (create/cancel)
--   Check-in   : checked_in + 1  (check-in)
--   FlightOps  : capacity        (kapasite güncelleme)
-- Hepsi aynı satırda ROW EXCLUSIVE + tuple lock (FOR NO KEY UPDATE) alır.
CREATE TABLE flightops.flight_inventory (
    flight_id   bigint PRIMARY KEY REFERENCES flightops.flight(flight_id),
    capacity    integer NOT NULL CHECK (capacity > 0),
    sold        integer NOT NULL DEFAULT 0,
    checked_in  integer NOT NULL DEFAULT 0,
    version     bigint  NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT inv_sold_chk      CHECK (sold >= 0 AND sold <= capacity),
    CONSTRAINT inv_checkedin_chk CHECK (checked_in >= 0 AND checked_in <= sold)
);

CREATE TABLE flightops.ops_event (
    event_id    bigserial PRIMARY KEY,
    flight_id   bigint NOT NULL REFERENCES flightops.flight(flight_id),
    event_type  varchar(32) NOT NULL,
    detail      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ops_event_flight_idx ON flightops.ops_event (flight_id, created_at);

-- =========================================================================
-- RESERVATION (sahip: svc_reservation)
-- =========================================================================
CREATE TABLE reservation.passenger (
    passenger_id bigserial PRIMARY KEY,
    first_name   varchar(64)  NOT NULL,
    last_name    varchar(64)  NOT NULL,
    email        varchar(128) NOT NULL,
    created_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE reservation.booking (
    booking_id      bigserial PRIMARY KEY,
    pnr             varchar(8)  NOT NULL UNIQUE,
    flight_id       bigint      NOT NULL REFERENCES flightops.flight(flight_id),
    passenger_id    bigint      NOT NULL REFERENCES reservation.passenger(passenger_id),
    status          varchar(16) NOT NULL DEFAULT 'CONFIRMED' CHECK (status IN ('CONFIRMED','CANCELLED')),
    fare_class      char(1)     NOT NULL DEFAULT 'Y' CHECK (fare_class IN ('Y','B','J')),
    price           numeric(10,2) NOT NULL CHECK (price >= 0),
    idempotency_key varchar(64) UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    cancelled_at    timestamptz
);
CREATE INDEX booking_flight_status_idx ON reservation.booking (flight_id, status);
-- Not: created_at üzerinde bilinçli olarak indeks yok; route-demand raporu
-- maliyetli (seq scan + agregasyon) bir sorgu olarak kullanılır.

-- =========================================================================
-- CHECK-IN (sahip: svc_checkin)
-- =========================================================================
CREATE TABLE checkin.checkin (
    checkin_id    bigserial PRIMARY KEY,
    booking_id    bigint NOT NULL UNIQUE REFERENCES reservation.booking(booking_id),
    flight_id     bigint NOT NULL REFERENCES flightops.flight(flight_id),
    channel       varchar(16) NOT NULL DEFAULT 'WEB' CHECK (channel IN ('WEB','KIOSK','COUNTER','MOBILE','API')),
    checked_in_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX checkin_flight_idx ON checkin.checkin (flight_id);

CREATE TABLE checkin.seat_assignment (
    flight_id   bigint     NOT NULL REFERENCES flightops.flight(flight_id),
    seat_no     varchar(4) NOT NULL CHECK (seat_no ~ '^[1-9][0-9]?[A-F]$'),
    booking_id  bigint     NOT NULL UNIQUE REFERENCES checkin.checkin(booking_id),
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (flight_id, seat_no)
);

-- =========================================================================
-- BAGGAGE (sahip: svc_baggage)
-- booking/checkin'e BİLİNÇLİ OLARAK FK yok: FK kontrolü booking satırında
-- FOR KEY SHARE alır ve iptal (FOR UPDATE) ile çakışırdı. Baggage'ın
-- bağımsız/kontrol grubu yolu olarak kalması için doğrulama kilitsiz SELECT ile yapılır.
-- =========================================================================
CREATE TABLE baggage.bag (
    bag_id          bigserial PRIMARY KEY,
    tag             varchar(12) NOT NULL UNIQUE,
    booking_id      bigint      NOT NULL,
    flight_id       bigint      NOT NULL,
    weight_kg       numeric(5,2) NOT NULL CHECK (weight_kg > 0 AND weight_kg <= 50),
    status          varchar(16) NOT NULL DEFAULT 'CHECKED'
                    CHECK (status IN ('CHECKED','LOADED','IN_TRANSIT','ARRIVED','DELIVERED','MISHANDLED')),
    idempotency_key varchar(64) UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX bag_booking_idx ON baggage.bag (booking_id);
CREATE INDEX bag_flight_idx  ON baggage.bag (flight_id);

CREATE TABLE baggage.bag_event (
    event_id    bigserial PRIMARY KEY,
    bag_id      bigint      NOT NULL REFERENCES baggage.bag(bag_id),
    flight_id   bigint      NOT NULL,
    event_type  varchar(16) NOT NULL,
    location    varchar(16) NOT NULL,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX bag_event_bag_idx ON baggage.bag_event (bag_id, created_at);
-- Not: bag_event.flight_id üzerinde bilinçli olarak indeks yok; bag-history
-- raporu geniş tablo taraması yapan I/O ağırlıklı sorgudur.

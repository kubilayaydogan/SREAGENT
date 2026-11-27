# Mimari

```
                        Yönetim ağı (tarayıcı / SSH)
                                  │ 8080-8084 (yalnız ADMIN_CIDR, firewalld)
┌─────────────────────────────────┴────────────────────────────────────────────┐
│ APP_VM — Rocky Linux 9, Docker, OneAgent full-stack, (Environment ActiveGate) │
│  docker network airlab 172.28.0.0/24                                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐          │
│  │ reservation  │ │ checkin      │ │ flightops    │ │ baggage      │  Java 21 │
│  │ :8081        │ │ :8082        │ │ :8083        │ │ :8084        │  Boot 4.1│
│  │ svc_reserv.  │ │ svc_checkin  │ │ svc_flightops│ │ svc_baggage  │  Hikari 8│
│  └──────▲───────┘ └──────▲───────┘ └──────▲───────┘ └──────▲───────┘          │
│         └──── HTTP (REST + /lab/** token+allowlist) ───────┘                  │
│                         ┌──────┴───────┐   ┌──────────────────┐               │
│                         │ chaos :8080  │   │ pg-lock-collector│  read-only    │
│                         │ DB bağlantısı│   │ lab_monitor      │──────┐        │
│                         │ YOK          │   └──────────────────┘      │        │
│                         └──────────────┘                              │        │
└───────────┬──────────────────────────────────────────────────────────┼────────┘
            │ JDBC 5432 (4 rol, ayrı application_name)                  │
┌───────────▼──────────────────────────────────────────────────────────▼────────┐
│ DB_VM — Rocky Linux 9, postgresql-17 (PGDG, systemd), OneAgent (infra)        │
│  DB airlab: şemalar flightops / reservation / checkin / baggage / lab         │
│  pg_stat_statements, compute_query_id, track_io_timing, log_lock_waits        │
│  firewalld: 5432 yalnız APP_VM (ve ActiveGate) IP'sine                        │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Bileşenler

| Bileşen | Sorumluluk | Teknoloji |
|---|---|---|
| reservation-service | uçuş arama, rezervasyon oluşturma/iptal, maliyetli rota raporu | Spring MVC + Thymeleaf + JdbcClient |
| checkin-service | rezervasyon sorgu, check-in, koltuk | aynı |
| flightops-service | uçuş listesi/detay, durum/kapı/gecikme, kapasite, doluluk raporu | aynı |
| baggage-service | bagaj kaydı, olay akışı, durum, I/O ağırlıklı geçmiş raporu | aynı |
| lab-common | request/experiment kimliği (MDC + SQL yorumu), hata sınıflandırma, erişim logu, lab uçları (hold-connection, pressure pool, cleanup) | kütüphane |
| db-migrator | **tek** Flyway runner (`migrate`), `seed --scale`, onaylı `reset`, `check` | Flyway 12, Java 21 |
| chaos-controller | deney tanımı, fazlar, RPS/concurrency üreticileri, arka plan trafiği, stop-all, kayıt | Spring MVC + virtual threads + HdrHistogram |
| pg-lock-collector | blocking zinciri, oturum, kapasite, statement delta → Dynatrace | Python 3.13 + psycopg 3 |

## Ana kararlar (ayrıntı: `docs/lab/decisions/`)
- ADR-001 sürümler; ADR-002 JDBC + elle transaction (TransactionTemplate) — SQL ve kilit sırası görünür kalsın;
  ADR-003 tek DB / şema başına sahiplik / servis başına rol; ADR-004 chaos yalnız HTTP, lab uçları sınırlı;
  ADR-005 collector neden var; ADR-006 ana havuz ve pressure havuzu ayrımı.

## Emniyet sınırları (savunmada derinlik)

| Katman | Sınır |
|---|---|
| chaos-controller | toplam RPS ≤ 600 (deney; sürekli trafik ayrıca ≤ 20 RPS/uygulama), hedef RPS ≤ 150, concurrency ≤ 64/hedef, 128 toplam, maxInFlight ≤ 256, süre ≤ 3600 sn, holdMs ≤ 5000, timeout ≤ 30 sn; tek aktif deney; kuyruk yok |
| uygulama | `/lab/**` yalnız `lab` profili + `X-Lab-Token` (≥16 karakter, sabit zamanlı karşılaştırma) + CIDR allowlist; hold ≤ `LAB_MAX_HOLD_MS`; eşzamanlı hold ≤ 40; Tomcat 50 thread; tx timeout 10 sn; keyfi SQL yok |
| PostgreSQL | rol bazlı `statement_timeout 15s`, `lock_timeout 8s`, `idle_in_transaction_session_timeout 30s`, `CONNECTION LIMIT 40`; global `client_connection_check_interval 2s`; reserved slotlar izleme rolleri için |
| durdurma | stop-all → üretim durur → uçuştaki istekler kendi timeout'una kadar biter → her servise `/lab/cleanup` (kendi rolünün `lab:` işaretli backend'lerini `pg_cancel_backend`, pressure havuzunu kapat) |

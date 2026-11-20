# airlab — Havacılık SRE laboratuvarı (Aşama 1)

Dört Java 21 / Spring Boot iş uygulaması (Reservation, Check-in, Flight Operations, Baggage), ortak PostgreSQL 17,
yalnız HTTP ile yük veren **chaos-controller** ve Dynatrace gözlemlenebilirlik hazırlığı.
Aşama 2 (Dynatrace MCP üzerinden çalışan Python SRE Agent) **henüz yazılmadı**.

- Topoloji: APP_VM (Rocky 9/10, Docker) ↔ DB_VM (Rocky 9, postgresql-17 systemd) — `docs/architecture.md`
- Tablo/transaction/kilit bağımlılıkları — `docs/sql-dependencies.md`
- Deney reçeteleri ve yerel ölçümler — `docs/experiments.md`
- Telemetri sözleşmesi (Aşama 2 girdisi) — `docs/telemetry-contract.md`
- Kurulum / işletim / acil durdurma — `docs/runbook.md` · bu ortama özel sıralı plan — `docs/deploy-plan.md`
- Kabul kapısı — `docs/acceptance-gate.md` · Durum — `PROJECT_STATE.md`
- Sürüm ve tasarım kararları — `docs/decisions/` · OpenAPI — `docs/openapi/*.yaml`

## Hızlı başlangıç (yerel, yalnız Docker gerekir)
```bash
scripts/build.sh                                  # Docker içinde mvn verify: birim + Testcontainers PG17 testleri
cd app-vm && cp .env.example .env.local           # DB_HOST=postgres ve LOCAL_PG_SUPERUSER_PASSWORD ekleyin, parolaları değiştirin
C="docker compose -f docker-compose.yml -f compose.local.yml --env-file .env.local"
$C up -d --build postgres
$C --profile tools run --rm db-migrator migrate
$C --profile tools run --rm db-migrator seed --scale=1
$C --profile observability up -d
cd .. && scripts/smoke.sh                          # 4 UI → API → DB doğrulaması
CHAOS_PASSWORD=... python3 scripts/run-experiment.py E2-hot-row-lock --baseline 40 --load 50 --recovery 30
```
Hedef VM kurulumu: `docs/runbook.md`.

## Depo yapısı
```
pom.xml                    Maven çok modüllü (Java 21, Spring Boot 4.1.3)
lab-common/                istek bağlamı, SQL etiketleme, hata sınıflandırma, erişim logu, /lab/** uçları
db-migrator/               TEK Flyway runner + seed + onaylı reset + integrity check; SQL: src/main/resources/db/migration
test-support/              Testcontainers PG17 + gerçek rol betiği + migration + seed
reservation-service/ checkin-service/ flightops-service/ baggage-service/
chaos-controller/          panel + REST, RPS/concurrency üreticileri, reçeteler (resources/presets)
Dockerfile                 çok hedefli imaj (reservation|checkin|flightops|baggage|chaos|migrator)
app-vm/                    docker-compose.yml, compose.local.yml (yalnız yerel PG), .env.example, install-docker.sh
db-vm/                     install-postgres17.sh, bootstrap-db.sh, create-roles.sql, postgresql.lab.conf, verify-db-vm.sh
observability/             pg-lock-collector (Python), dynatrace/ (yerleşim, kurulum, token kapsamları)
scripts/                   build.sh/.ps1, smoke.sh, run-experiment.py, lock-chain-demo.sh, verify-db.sh, export-openapi.sh
docs/                      mimari, SQL bağımlılıkları, deneyler, telemetri sözleşmesi, runbook, kabul kapısı, ADR, OpenAPI
```

## Test komutları
| Komut | Ne yapar |
|---|---|
| `scripts/build.sh` (veya `.\scripts\build.ps1`) | 29 test: lab-common birim (3), migrator IT (2), 4 servis IT (13, gerçek PG17), chaos motoru (6), + collector pytest ayrı (5) |
| `docker run --rm -v "$PWD/observability/pg-lock-collector":/w -w /w python:3.13-slim sh -c "pip install -q pytest==9.2.0 'psycopg[binary]==3.3.9' && python -m pytest -q tests"` | collector birim testleri |
| `scripts/smoke.sh` | çalışan yığında 31 uçtan uca kontrol |
| `scripts/lock-chain-demo.sh` | tek seferlik gerçek blocking zinciri |
| `scripts/verify-db.sh` | bütünlük, rol/app eşlemesi, kapasite |

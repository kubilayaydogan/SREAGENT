# airlab — Havacılık SRE laboratuvarı (Aşama 1)

Dört Java 21 / Spring Boot iş uygulaması (Reservation, Check-in, Flight Operations, Baggage), ortak PostgreSQL 17,
yalnız HTTP ile yük veren **chaos-controller** ve Dynatrace gözlemlenebilirlik hazırlığı.
Aşama 2: Dynatrace MCP üzerinden çalışan salt-okunur Python SRE Agent — `sre-agent/`.
SRE Operations Platform (PostgreSQL hafızası, FastAPI, React arayüz `:8090`, watch/zamanlanmış kontroller) — `sre-platform/README.md`.
Chaos Controller v2: 20 gerçek senaryo, adaptive control, safety budget, recovery doğrulaması, React UI `:8080/ui/`, API `/api/v2` — `docs/lab/chaos-v2-architecture.md`.

- Topoloji: APP_VM (Rocky 9/10, Docker) ↔ DB_VM (Rocky 9, postgresql-17 systemd) — `docs/lab/architecture.md`
- Tablo/transaction/kilit bağımlılıkları — `docs/lab/sql-dependencies.md`
- Deney reçeteleri ve yerel ölçümler — `docs/lab/experiments.md`
- Telemetri sözleşmesi (Aşama 2 girdisi) — `docs/lab/telemetry-contract.md`
- Kurulum / işletim / acil durdurma — `docs/lab/runbook.md` · bu ortama özel sıralı plan — `docs/lab/deploy-plan.md`
- Kabul kapısı — `docs/lab/acceptance-gate.md` · Durum — `PROJECT_STATE.md`
- Sürüm ve tasarım kararları — `docs/lab/decisions/` · OpenAPI — `docs/lab/openapi/*.yaml`

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
Hedef VM kurulumu: `docs/lab/runbook.md`.

## Depo yapısı
```
lab/                       Java 21 laboratuvarı: tek Maven reactor (lab/pom.xml) ve çok hedefli lab/Dockerfile
  lab-common/              istek bağlamı, SQL etiketleme, hata sınıflandırma, erişim logu, /lab/** uçları
  db-migrator/             TEK Flyway runner + seed + onaylı reset + integrity check; SQL: src/main/resources/db/migration
  test-support/            Testcontainers PG17 + gerçek rol betiği + migration + seed
  reservation-service/ checkin-service/ flightops-service/ baggage-service/
  chaos-controller/        v1 panel (/classic) + v2 motoru (/api/v2, chaos/v2/*) + React UI (ui/ → /ui/)
sre-agent/                 Python analiz motoru (airlab_agent): graph, signal/feature/scoring, MCP ve LLM adaptörleri, eval
sre-platform/              backend/ (FastAPI + worker + Alembic migration'ları), frontend/ (React)
observability/             pg-lock-collector (Python), netem-agent, dynatrace/ (yerleşim, kurulum, token kapsamları)
app-vm/                    docker-compose.yml (tüm servisler), compose.local.yml (yalnız yerel PG), .env.example
db-vm/                     install-postgres17.sh, bootstrap-db.sh, create-roles.sql, postgresql.lab.conf, verify-db-vm.sh
scripts/                   build.sh/.ps1, smoke.sh, run-experiment.py, lock-chain-demo.sh, verify-db.sh, export-openapi.sh
docs/lab/                  mimari, SQL bağımlılıkları, deneyler, telemetri sözleşmesi, runbook, kabul kapısı, ADR, OpenAPI
docs/platform/             SRE platformu: memory, guardrails, security, settings, agent builder, trace, örnek raporlar
docs/specs/                özgün gereksinim ve prompt metinleri (tarihsel; eski yolları içerebilir)
deploy/evidence/           kurulum ve deney kanıtları
```
Docker build context her zaman repo köküdür (`app-vm/docker-compose.yml` içinde `context: ..`); lab imajları
`lab/Dockerfile`, platform backend imajı `sre-platform/backend/Dockerfile` ile derlenir.

## Test komutları
| Komut | Ne yapar |
|---|---|
| `scripts/build.sh` (veya `.\scripts\build.ps1`) | 29 test: lab-common birim (3), migrator IT (2), 4 servis IT (13, gerçek PG17), chaos motoru (6), + collector pytest ayrı (5) |
| `docker run --rm -v "$PWD/observability/pg-lock-collector":/w -w /w python:3.13-slim sh -c "pip install -q pytest==9.2.0 'psycopg[binary]==3.3.9' && python -m pytest -q tests"` | collector birim testleri |
| `scripts/smoke.sh` | çalışan yığında 31 uçtan uca kontrol |
| `scripts/lock-chain-demo.sh` | tek seferlik gerçek blocking zinciri |
| `python3 scripts/chaos-e2e.py --quick` / `--acceptance` (APP_VM) | Chaos v2: 20 senaryo uçtan uca / S17 HIGH cascading acceptance; kanıt `deploy/evidence/chaos-v2/` |
| `scripts/verify-db.sh` | bütünlük, rol/app eşlemesi, kapasite |

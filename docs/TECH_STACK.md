# Teknoloji Yığını

Bu doküman projede kullanılan teknolojileri, sürümlerini ve her birinin nerede tanımlandığını listeler. Sürümler
doğrudan manifest dosyalarından, Dockerfile'lardan ve Compose dosyasından alınmıştır.

[← Dokümantasyon merkezine dön](../README.md)

## İçindekiler

- [Sürüm gösterimi](#sürüm-gösterimi)
- [Frontend](#frontend)
- [Backend](#backend)
- [Agent runtime ve orchestration](#agent-runtime-ve-orchestration)
- [LLM](#llm)
- [MCP](#mcp)
- [Veri depolama](#veri-depolama)
- [Observability](#observability)
- [Laboratuvar (izlenen sistem)](#laboratuvar-izlenen-sistem)
- [Container ve altyapı](#container-ve-altyapı)
- [Test ve kalite](#test-ve-kalite)
- [Kullanılmayan teknolojiler](#kullanılmayan-teknolojiler)
- [Tercih nedenleri](#tercih-nedenleri)

## Sürüm gösterimi

| Gösterim | Anlamı |
|---|---|
| `==1.2.24` ya da düz sürüm | **Sabitlenmiş.** Manifest tam bu sürümü ister. |
| `^19.1.0` | **Aralık.** npm aynı major içinde daha yeni sürümü kurabilir; kurulan kesin sürüm `package-lock.json` dosyasındadır. |
| `~5.8.3` | **Dar aralık.** Yalnız patch sürümü değişebilir. |
| `>=3.12` | **Alt sınır.** Daha yeni sürümler de kabul edilir. |
| lock dosyası | Python tarafında dolaylı bağımlılıklar dahil kesin sürümler [`sre-agent/requirements.lock`](../sre-agent/requirements.lock) ve [`sre-platform/backend/requirements.lock`](../sre-platform/backend/requirements.lock) dosyalarındadır; CI bunları kullanır. |
| "Spring Boot yönetir" | Sürüm projede yazılmaz; Spring Boot'un bağımlılık yönetiminden gelir. |

## Frontend

Kaynak: [`sre-platform/frontend/package.json`](../sre-platform/frontend/package.json),
[`sre-platform/frontend/Dockerfile`](../sre-platform/frontend/Dockerfile)

| Teknoloji | Sürüm | Kullanım amacı |
|---|---|---|
| React, React DOM | `^19.1.0` | Arayüz |
| TypeScript | `~5.8.3` | Tip kontrolü |
| Vite | `^6.3.5` | Build ve geliştirme sunucusu |
| Tailwind CSS (`@tailwindcss/vite`) | `^4.1.8` | Stil |
| React Router | `^7.6.0` | Sayfa yönlendirme |
| TanStack Query | `^5.80.0` | Sunucu durumu ve önbellek |
| Recharts | `^3.1.0` | Grafikler |
| @xyflow/react | `^12.12.1` | Agent Builder canvas'ı |
| Radix UI (dialog, tabs) | `^1.1.x` | Erişilebilir arayüz bileşenleri |
| react-markdown, remark-gfm | `^10.1.0`, `^4.0.1` | Agent yanıtlarını ve RCA raporlarını gösterme |
| lucide-react | `^0.511.0` | İkonlar |
| sonner | `^2.0.5` | Bildirimler |
| Node.js | 22 (alpine, build aşaması) | Arayüzü derleme |
| nginx | 1.29 (alpine) | Statik dosyalar ve `/api` reverse proxy |

## Backend

Kaynak: [`sre-platform/backend/pyproject.toml`](../sre-platform/backend/pyproject.toml),
[`sre-platform/backend/Dockerfile`](../sre-platform/backend/Dockerfile)

| Teknoloji | Sürüm | Kullanım amacı |
|---|---|---|
| Python | 3.13 (imaj: `python:3.13-slim`); paket gereksinimi `>=3.12` | Backend, worker ve agent |
| FastAPI | `==0.165.0` | HTTP API |
| Uvicorn | `==0.54.0` | ASGI sunucusu (tek worker) |
| sse-starlette | `==3.7.0` | Server-Sent Events ile canlı akış |
| SQLAlchemy (asyncio) | `==2.1.9` | ORM |
| Alembic | `==1.20.1` | Şema migration'ları |
| psycopg (binary, pool) | `==3.3.9` | PostgreSQL sürücüsü |
| PyJWT | `==2.15.1` | Oturum token'ları |
| jsonschema | `>=4.20` | Agent Builder girdi / çıktı şeması doğrulaması |
| PyYAML | `>=6.0` | Yapılandırma |

## Agent runtime ve orchestration

Kaynak: [`sre-agent/pyproject.toml`](../sre-agent/pyproject.toml)

| Teknoloji | Sürüm | Kullanım amacı |
|---|---|---|
| LangGraph | `==1.2.24` | Agent akışı (`StateGraph`) |
| LangChain | `==1.5.2` | Model ve tool soyutlamaları |
| langchain-core | `==1.8.4` | Mesaj tipleri, callback'ler |
| langgraph-checkpoint-postgres | `==3.2.1` | Checkpointer ve Store (platform `pyproject.toml` dosyasında) |
| Pydantic | `==2.14.4` | Structured output ve yapılandırma şemaları |
| PyYAML | `==6.0.4` | `defaults.yaml` |

## LLM

| Teknoloji | Sürüm | Kullanım amacı | Kaynak |
|---|---|---|---|
| langchain-anthropic | `==1.7.12` | Claude istemcisi | [`sre-agent/pyproject.toml`](../sre-agent/pyproject.toml) |
| Claude modeli | Varsayılan `claude-opus-5-5`; arayüzden `claude-sonnet-5-5` de seçilebilir | Soru yorumlama, yönlendirme, açıklama, RCA özeti | [`config.py`](../sre-agent/src/airlab_agent/config.py), [`model_switch.py`](../sre-platform/backend/sre_platform/services/model_switch.py) |

Model adı bir kod sürümü değil, yapılandırma değeridir: `CLAUDE_MODEL`, `CLAUDE_RCA_MODEL`, Settings'teki LLM profili
ya da üst bardaki seçimle değişir.

## MCP

Kaynak: [`sre-agent/pyproject.toml`](../sre-agent/pyproject.toml)

| Teknoloji | Sürüm | Kullanım amacı |
|---|---|---|
| `mcp` | `==1.30.0` | Model Context Protocol istemcisi (Streamable HTTP) |
| langchain-mcp-adapters | `==0.3.2` | MCP oturumu ve tool adapter'ı |
| Dynatrace Remote MCP | SaaS servis (sürüm projede sabit değil) | `execute-dql` tool'u |

## Veri depolama

| Teknoloji | Sürüm | Kullanım amacı | Kaynak |
|---|---|---|---|
| PostgreSQL | 17.7 (alpine) | Platform veritabanı (`sre` ve `langgraph` şemaları) | [`app-vm/docker-compose.yml`](../app-vm/docker-compose.yml) |
| PostgreSQL | 17 | İzlenen laboratuvar veritabanı (`airlab`) | [`db-vm/install-postgres17.sh`](../db-vm/install-postgres17.sh) |
| Fernet (`cryptography`) | `==50.0.2` (lock dosyası) | Settings secret deposunun şifrelenmesi | [`settings/secrets.py`](../sre-platform/backend/sre_platform/settings/secrets.py) |

## Observability

| Teknoloji | Sürüm | Kullanım amacı | Kaynak |
|---|---|---|---|
| Dynatrace SaaS (OneAgent, Metrics / Logs API v2) | SaaS | Agent'ın telemetri kaynağı | [`observability/dynatrace/README.md`](../observability/dynatrace/README.md) |
| Dynatrace PostgreSQL extension | 3.2.10 (laboratuvar kurulumunda) | `postgres.*` metrikleri | [`docs/lab/deploy-plan.md`](lab/deploy-plan.md) |
| LangSmith | `==0.16.0` | İsteğe bağlı tracing ve LLM maliyet verisi | [`sre-agent/pyproject.toml`](../sre-agent/pyproject.toml) |
| `pg-lock-collector` | Repository içi (Python 3.13) | Laboratuvar veritabanından lock ve statement metrikleri | [`observability/pg-lock-collector/`](../observability/pg-lock-collector/) |
| `netem-agent` | Repository içi (Python 3.13) | Laboratuvarda ağ gecikmesi enjeksiyonu | [`observability/netem-agent/`](../observability/netem-agent/) |

Platformun kendi izleme kayıtları (Agent Trace, MCP Çağrıları, Context Windows, Middleware ve Guardrails kayıtları)
harici bir araç gerektirmez; platform veritabanında tutulur.

## Laboratuvar (izlenen sistem)

Kaynak: [`lab/pom.xml`](../lab/pom.xml), [`lab/Dockerfile`](../lab/Dockerfile)

| Teknoloji | Sürüm | Kullanım amacı |
|---|---|---|
| Java | 21 (Eclipse Temurin) | Dört iş servisi ve Chaos Controller |
| Spring Boot | 4.1.3 | Servis çatısı |
| Maven | 3.9 (build imajı) | Derleme |
| springdoc-openapi | 3.2.0 | Laboratuvar OpenAPI dokümanları |
| Flyway | Spring Boot yönetir | Laboratuvar şema migration'ları (`db-migrator`) |
| HikariCP, Micrometer | Spring Boot yönetir | Connection Pool ve metrikler |
| React (Chaos Controller arayüzü) | [`lab/chaos-controller/ui/package.json`](../lab/chaos-controller/ui/package.json) | Chaos Controller v2 arayüzü |

## Container ve altyapı

| Teknoloji | Sürüm | Kullanım amacı | Kaynak |
|---|---|---|---|
| Docker Engine | Sabitlenmemiş | Derleme ve çalıştırma | – |
| Docker Compose | v2 sözdizimi; v5.8.0 ile doğrulandı | Tüm servislerin tanımı | [`app-vm/docker-compose.yml`](../app-vm/docker-compose.yml) |
| GitHub Actions | `ubuntu-24.04` runner | CI | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) |
| Rocky Linux | 10 (doğrulanan host) | Kurulumun çalıştığı işletim sistemi | [`docs/lab/deploy-plan.md`](lab/deploy-plan.md) |

## Test ve kalite

| Teknoloji | Sürüm | Kullanım amacı | Kaynak |
|---|---|---|---|
| pytest | `==9.2.0` | Agent ve backend testleri | [`sre-platform/backend/pyproject.toml`](../sre-platform/backend/pyproject.toml) |
| httpx | `==0.28.1` (lock dosyası) | API testleri | aynı dosya |
| Vitest | `^3.2.0` | Frontend testleri | [`package.json`](../sre-platform/frontend/package.json) |
| Testing Library (react) | `^16.3.0` | Bileşen testleri | aynı dosya |
| jsdom | `^26.1.0` | Test ortamı | aynı dosya |
| JUnit, Testcontainers (PostgreSQL 17) | Spring Boot yönetir | Laboratuvar entegrasyon testleri | [`lab/pom.xml`](../lab/pom.xml) |
| gitleaks | CI'da kurulur | Secret taraması (tüm Git geçmişi) | [`.gitleaks.toml`](../.gitleaks.toml), [`ci.yml`](../.github/workflows/ci.yml) |
| pip-audit, npm audit | CI'da çalışır | Bağımlılık güvenlik denetimi | [`ci.yml`](../.github/workflows/ci.yml) |

## Kullanılmayan teknolojiler

Yanlış beklenti oluşmaması için açıkça belirtilir:

| Teknoloji | Durum |
|---|---|
| pgvector, embedding, vektör arama | Yok. Memory benzerliği deterministik parmak izi ve tam metin aramasıyla hesaplanır. |
| Anthropic dışındaki LLM provider'ları | Doğrulanmış adapter yok; arayüzde listelenir ama oluşturulamaz. |
| OpenTelemetry export | Yok; SDK kurulu değil. |
| Cron ifadeleri | Yok; zamanlama aralık tabanlıdır. |
| LangChain `create_agent` | Kullanılmaz; iki özel LangGraph `StateGraph` vardır. |
| Kubernetes, Helm | Yok; dağıtım Docker Compose iledir. |
| TLS sonlandırma | Platformda yok; arayüz düz HTTP ile sunulur. |

## Tercih nedenleri

Aşağıdaki nedenler repository'deki karar kayıtlarına dayanır.

| Karar | Gerekçe | Kaynak |
|---|---|---|
| Deterministik scoring, LLM yalnız açıklama için | Skorun ve Root Cause atamasının tekrarlanabilir ve denetlenebilir olması | [ADR-007](lab/decisions/ADR-007-sre-agent.md) |
| Tek veri kaynağı olarak Dynatrace Remote MCP | Agent'ın veritabanına ya da host'lara doğrudan erişmemesi (salt okunur sınır) | [ADR-007](lab/decisions/ADR-007-sre-agent.md) |
| Laboratuvar sürümleri (Java, Spring Boot, PostgreSQL) | Sürüm seçimi kayıtlıdır | [ADR-001](lab/decisions/ADR-001-versions.md) |
| Embedding kullanılmaması | Anthropic API embedding sunmaz; ek provider ve anahtar gerekirdi | [agent-memory.md](platform/agent-memory.md) |
| İkinci bir retry katmanı eklenmemesi | Gateway ve SDK zaten retry eder; ek katman deneme sayısını katlardı | [agent-middleware.md](platform/agent-middleware.md) |

> **Mimari değerlendirme (karar kaydı yok):** React, FastAPI ve PostgreSQL seçimleri için repository'de yazılı bir
> gerekçe bulunmuyor. Bu seçimler; tek veritabanında hem ilişkisel kayıt hem LangGraph checkpoint tutabilme, SSE
> desteği ve tek dilli (Python) agent / backend entegrasyonu açısından tutarlıdır, ancak bu bir yorumdur.

# SRE AI Agent — Full-Stack Platform Development

Sen deneyimli bir **Principal Software Architect, Senior Python Backend Engineer, Senior React Engineer, Database Architect ve Site Reliability Engineer** olarak hareket edeceksin.

Mevcut SRE AI Agent projemi kurumsal seviyede, modern bir web arayüzüne, PostgreSQL tabanlı kalıcı veri yönetimine ve gelişmiş operasyonel yeteneklere sahip bir **AI-Powered SRE Operations Platform** hâline getirmeni istiyorum.

Bu projeyi sıfırdan yazmanı istemiyorum. **Öncelikle mevcut repository'yi analiz et, çalışan kodları ve entegrasyonları koru, ardından gerekli geliştirmeleri yap.**

Amacımız yalnızca bir AI chatbot geliştirmek değil; Dynatrace gözlemlenebilirlik verilerini kullanan, geçmiş araştırmaları hatırlayan, incident'ları yöneten, kök neden analizleri üreten, kanıtlarını saklayan ve kullanıcılara profesyonel bir SRE operasyon ekranı sunan bir platform oluşturmak.

---

# 1. EXISTING PROJECT CONTEXT

Mevcut altyapım:

- Rocky Linux üzerinde çalışan uygulama sunucusu.
- Docker üzerinde çalışan dört adet Java uygulaması.
- Java uygulamalarının ortak kullandığı PostgreSQL veritabanı.
- PostgreSQL'in ayrı bir Rocky Linux sunucusunda çalışması.
- Dynatrace OneAgent ve PostgreSQL Extension entegrasyonu.
- Dynatrace MCP Server üzerinden veri alan Python tabanlı SRE Agent.
- LangChain / LangGraph tabanlı Agent orkestrasyonu.
- Dört Java uygulamasında kontrollü yük oluşturabilen Chaos Application.

Mevcut sistemin gerçekten hangi bileşenlerden oluştuğunu repository üzerinde doğrula.

Mevcut Agent yapısını, kullanılan kütüphaneleri, Docker Compose dosyalarını, API'leri, environment değişkenlerini ve Dynatrace MCP bağlantılarını incele.

Eksik bileşenleri belirle.

Çalışan entegrasyonları değiştirmeden genişlet.

# 2. TARGET ARCHITECTURE

Hedef mimari:

Frontend:
- React
- TypeScript
- Vite
- Tailwind CSS
- shadcn/ui
- Recharts
- TanStack Query
- React Router

Backend:
- Python
- FastAPI
- Pydantic
- SQLAlchemy 2.x
- Alembic
- Async PostgreSQL Driver

AI Agent:
- LangChain
- LangGraph
- Existing LLM integration
- Dynatrace MCP
- PostgreSQL Checkpointer
- Persistent Agent Memory

Database:
- PostgreSQL
- Optional pgvector extension

Infrastructure:
- Docker
- Docker Compose
- Environment-based configuration
- Healthchecks
- Structured logging

Frontend hiçbir zaman doğrudan LLM, PostgreSQL veya Dynatrace MCP Server ile iletişim kurmamalıdır.

Bütün işlemler FastAPI backend üzerinden yönetilmelidir.

# 3. DATABASE ARCHITECTURE

SRE Agent'ın kendisine ait ayrı bir PostgreSQL veritabanı olmalıdır.

Bu veritabanını, dört Java uygulamasının kullandığı PostgreSQL veritabanından bağımsız tasarla.

Amaç, test ortamındaki PostgreSQL veritabanında kontrollü sorun oluşturulduğunda SRE Agent'ın kendi veritabanının etkilenmemesidir.

Yeni PostgreSQL servisini Docker Compose üzerinden çalıştır.

Aşağıdaki temel tabloları oluştur:

## 3.1 Conversations

Kullanıcı ile Agent arasındaki konuşma oturumları.

Alanlar:
- id
- user_id
- title
- thread_id
- created_at
- updated_at

## 3.2 Messages

Konuşma mesajları.

Alanlar:
- id
- conversation_id
- role
- content
- metadata
- created_at

## 3.3 Investigations

Agent tarafından gerçekleştirilen araştırmalar.

Alanlar:
- id
- conversation_id
- application_id
- title
- description
- status
- severity
- started_at
- completed_at
- duration_ms
- root_cause_hypothesis
- confidence
- created_at
- updated_at

## 3.4 Incidents

Tespit edilen operasyonel olaylar.

Alanlar:
- id
- investigation_id
- application_id
- title
- description
- severity
- status
- detected_at
- acknowledged_at
- resolved_at
- root_cause
- created_at
- updated_at

Incident durumları:

OPEN → INVESTIGATING → MITIGATED → RESOLVED

## 3.5 Investigation Evidence

Araştırma sırasında elde edilen kanıtlar.

Alanlar:
- id
- investigation_id
- evidence_type
- source
- entity_id
- trace_id
- span_id
- metric_name
- query_reference
- timestamp
- content
- metadata

Evidence türleri:
- METRIC
- LOG
- TRACE
- EVENT
- PROBLEM
- TOPOLOGY

## 3.6 Agent Tool Executions

Agent'ın çağırdığı araçların geçmişi.

Alanlar:
- id
- investigation_id
- tool_name
- tool_arguments
- execution_status
- duration_ms
- error_message
- created_at

## 3.7 RCA Reports

Kök neden analiz raporları.

Alanlar:
- id
- investigation_id
- incident_id
- summary
- suspected_root_cause
- supporting_evidence
- contributing_factors
- impact_analysis
- recommended_actions
- confidence
- generated_at

## 3.8 Agent Feedback

Kullanıcı değerlendirmeleri.

Alanlar:
- id
- investigation_id
- user_id
- rating
- is_correct
- comment
- created_at

## 3.9 Known Issues

Önceden yaşanan ve doğrulanan sorunlar.

Alanlar:
- id
- application_id
- title
- description
- symptoms
- root_cause
- resolution
- created_at
- updated_at

## 3.10 Runbooks

Operasyonel müdahale prosedürleri.

Alanlar:
- id
- title
- description
- application_id
- procedure
- risk_level
- requires_approval
- created_at
- updated_at

Gerekli foreign key, index, unique constraint ve veri bütünlüğü kurallarını oluştur.

Veritabanı şemasını yalnızca bu alanlarla sınırlama. Mimari açıdan gerekli ek tabloları ve alanları tasarlayabilirsin.

Veritabanı migration işlemleri Alembic üzerinden yönetilmelidir.

# 4. AGENT MEMORY SYSTEM

Agent'a kalıcı hafıza kazandır.

## 4.1 Short-Term Memory

LangGraph PostgreSQL Checkpointer kullan.

Amaç:
- Konuşma durumlarını saklamak.
- Konuşmayı kaldığı yerden devam ettirmek.
- Agent execution state bilgisini korumak.
- Gerektiğinde interrupted workflow devamlılığı sağlamak.

## 4.2 Long-Term Memory

LangGraph PostgreSQL Store veya uygun bir PostgreSQL persistence mekanizması kullan.

Agent aşağıdaki soruları yanıtlayabilmelidir:

- Bu uygulamada daha önce benzer bir hata yaşandı mı?
- Geçen hafta oluşan PostgreSQL connection pool sorunları nelerdi?
- Önceki incident nasıl çözülmüştü?
- Daha önce hangi RCA raporlarını oluşturdun?
- Bu problem için kayıtlı bir runbook var mı?
- Son araştırmamızda ne tespit etmiştik?

Memory sistemi kullanıcı, konuşma, uygulama ve incident bağlamını ayırt etmelidir.

PostgreSQL full-text search kullanarak geçmiş kayıtları arayabilmelidir.

Gerekli görülürse pgvector ile semantic search eklenebilir.

# 5. BACKEND API

FastAPI üzerinde modüler bir REST API geliştir.

Önerilen endpoint'ler:

Authentication:
- POST /api/auth/login
- GET /api/auth/me

Applications:
- GET /api/applications
- GET /api/applications/{id}/health
- GET /api/applications/{id}/metrics

Conversations:
- POST /api/conversations
- GET /api/conversations
- GET /api/conversations/{id}
- GET /api/conversations/{id}/messages

Agent:
- POST /api/agent/investigate
- POST /api/agent/chat
- GET /api/agent/stream/{id}

Investigations:
- GET /api/investigations
- GET /api/investigations/{id}
- GET /api/investigations/{id}/evidence
- GET /api/investigations/{id}/tools

Incidents:
- GET /api/incidents
- POST /api/incidents
- GET /api/incidents/{id}
- PATCH /api/incidents/{id}

RCA:
- GET /api/rca
- GET /api/rca/{id}
- POST /api/rca/{investigation_id}/generate

Knowledge Base:
- GET /api/known-issues
- GET /api/runbooks

Feedback:
- POST /api/feedback

Bu endpoint'leri mevcut backend yapısını inceleyerek uyarlayabilirsin.

API'ler Pydantic modelleriyle tip güvenli olmalı.

Tutarlı HTTP status code, validation ve exception handling uygulanmalı.

Listeleme endpoint'lerinde pagination, filtering ve sorting bulunmalı.

Endpoint'ler yalnızca taslak olarak bırakılmamalı; ilgili servis ve persistence katmanlarına bağlanmalıdır.

# 6. STREAMING ARCHITECTURE

Agent yanıtlarını frontend'e SSE üzerinden aktar.

Kullanıcı bir soru sorduğunda:

1. Frontend FastAPI'ye istek gönderir.
2. Backend Agent execution başlatır.
3. Agent gerektiğinde Dynatrace MCP araçlarını çağırır.
4. Tool execution olayları frontend'e stream edilir.
5. Agent'ın ürettiği yanıt token'ları mümkün olduğunda canlı gösterilir.
6. Investigation sonucu PostgreSQL'e kaydedilir.
7. Kullanıcı tamamlanan analizi daha sonra tekrar açabilir.

SSE event türleri:

- investigation_started
- tool_started
- tool_completed
- tool_failed
- agent_thinking
- token
- evidence_found
- investigation_completed
- error

Agent'ın gizli düşünce zincirini gösterme. `agent_thinking` yalnızca kullanıcıya gösterilebilecek kısa işlem durumu veya ilerleme özeti içermelidir.

Streaming sırasında bağlantı kesintileri, iptal ve timeout senaryolarını yönet.

# 7. FRONTEND DEVELOPMENT

Kurumsal seviyede modern bir React arayüzü geliştir.

Tasarım yaklaşımı:

**Dynatrace + Grafana + Modern AI Chat Interface**

Arayüz özellikleri:
- Dark theme
- Responsive layout
- Modern sidebar navigation
- Dashboard cards
- Interactive charts
- Tables
- Search and filtering
- Loading skeletons
- Empty states
- Error states
- Toast notifications
- Consistent design system

Frontend'i tek bir büyük component olarak geliştirme.

Reusable component mimarisi kullan.

## 7.1 Dashboard

Ana ekran aşağıdaki bilgileri göstermelidir:

- Monitoring yapılan uygulama sayısı
- Healthy / Degraded / Down durumları
- Active incidents
- Open investigations
- Average response time
- P95 / P99 latency
- Error rate
- Throughput
- PostgreSQL connection utilization
- Recent investigations
- Recent incidents

Uygulamalar filtrelenebilmelidir.

Grafikler zaman aralığına göre değişebilmelidir.

Dynatrace'ten gelen metrikler için uygun grafikler oluştur.

Canlı veri bulunmadığında sahte production verisi gösterme.

## 7.2 AI Investigation

ChatGPT benzeri profesyonel bir AI sohbet arayüzü geliştir.

Özellikler:
- Conversation sidebar
- New conversation
- Previous conversation history
- Markdown rendering
- Streaming responses
- Application selection
- Time range selection
- Investigation status
- Tool execution indicators
- Evidence references
- RCA summary
- Suggested follow-up questions

Kullanıcı şu tür sorular sorabilmelidir:

"Reservation uygulaması neden yavaşladı?"

"Son 30 dakikada PostgreSQL bağlantı sorunları var mı?"

"Bu incident daha önce yaşandı mı?"

"Önceki araştırmalarla karşılaştır."

"Son 24 saatin hata oranlarını analiz et."

## 7.3 Agent Activity

Agent'ın çalışma adımlarını görselleştir.

Örneğin:

Investigation Started

→ Fetch Application Metrics

→ Query PostgreSQL Metrics

→ Search Error Logs

→ Retrieve Distributed Traces

→ Correlate Evidence

→ Generate RCA

→ Save Investigation

Her adım için:
- Tool name
- Execution status
- Duration
- Error information
- Evidence references

göster.

## 7.4 Investigation History

PostgreSQL'e kaydedilen geçmiş araştırmaları listele.

Özellikler:
- Application filter
- Date range
- Severity filter
- Status filter
- Keyword search
- Investigation detail
- Associated evidence
- Previous RCA reports

## 7.5 Incident Management

Incident yönetimi ekranı geliştir.

Özellikler:
- Open incidents
- Investigating incidents
- Mitigated incidents
- Resolved incidents
- Severity badges
- Incident timeline
- Related investigations
- Root cause
- Recommended actions

Incident yaşam döngüsü veritabanında saklanmalı.

## 7.6 RCA Reports

Kök neden analiz raporlarını görüntüle.

Rapor aşağıdaki bölümleri içermeli:
- Executive summary
- Problem description
- Timeline
- Impact analysis
- Evidence
- Root cause hypothesis
- Contributing factors
- Recommended actions
- Confidence / uncertainty

RCA raporları sonradan yeniden açılabilmeli.

Gerekirse Markdown export desteği ekle.

## 7.7 Knowledge Base

Known Issues ve Runbooks için ayrı bir sayfa geliştir.

Kullanıcı:
- Bilinen sorunları arayabilmeli.
- İlgili geçmiş incident'ları görebilmeli.
- Runbook okuyabilmeli.
- Önceki çözüm kayıtlarını inceleyebilmeli.

# 8. ADVANCED SRE CAPABILITIES

PostgreSQL'in getirdiği kalıcı veri yönetimini aşağıdaki yeteneklerle kullan.

## 8.1 Historical RCA Comparison

Agent geçmiş RCA kayıtlarını arayabilmeli.

Yeni incident ile geçmiş incident'ları karşılaştırabilmeli.

Benzer bulguları ve önceki çözüm yöntemlerini kullanıcıya önerebilmeli.

## 8.2 Evidence Correlation

Metrics, logs, traces ve problems verilerini ilişkilendir.

Correlation sırasında:
- Application/entity ID
- Trace ID
- Span ID
- Timestamp
- Service dependency
- Error patterns

bilgilerini kullan.

Yalnızca kanıtların desteklediği sonuçları üret.

Correlation ile nedensellik arasındaki farkı koru.

## 8.3 Agent Feedback

Kullanıcı RCA sonucunu değerlendirebilmeli.

Örneğin:
- Correct
- Partially Correct
- Incorrect

Bu kayıtları PostgreSQL'e yaz.

İleride Agent evaluation süreçlerinde kullanılabilecek şekilde tasarla.

Feedback verisini otomatik model eğitimi gibi değerlendirme.

## 8.4 Scheduled Health Checks

Periyodik uygulama sağlık kontrolleri için genişletilebilir bir yapı tasarla.

Scheduled jobs PostgreSQL'de saklanabilmeli.

Çalıştırma için uygun scheduler/worker mimarisi oluştur.

Aynı görevin birden fazla worker tarafından yanlışlıkla iki kez çalıştırılmasını önle.

## 8.5 Human-in-the-Loop

Agent varsayılan olarak read-only çalışmalıdır.

Aşağıdaki işlemler kullanıcı onayı olmadan gerçekleştirilemez:

- Restart
- Rollback
- Database configuration changes
- SQL write operations
- Infrastructure modifications
- Container stop/restart

Bu işlemler için ayrı approval workflow tasarla.

MVP kapsamında otomatik remediation zorunlu değildir.

# 9. DATA MANAGEMENT PRINCIPLES

Dynatrace gözlemlenebilirlik verilerinin ana kaynağı olarak kalmalıdır.

PostgreSQL'e tüm ham metrikleri, logları ve trace'leri kopyalama.

Yalnızca analiz için gerekli kayıtları ve referansları sakla.

Örneğin:
- Investigation metadata
- Evidence summary
- Query references
- Trace IDs
- Entity IDs
- RCA reports
- Incident lifecycle
- Tool execution history

Hassas bilgileri maskele.

API token, secret ve credential değerlerini veritabanında veya frontend'de açık metin olarak saklama.

Uygun veritabanı index'leri, bağlantı havuzu ayarları ve veri saklama politikaları oluştur.

# 10. DOCKER & DEPLOYMENT

Mevcut Docker Compose mimarisini incele.

Frontend, Backend ve SRE PostgreSQL servislerini mevcut yapıya entegre et.

Servisler:
- sre-frontend
- sre-backend
- sre-postgres

Mevcut servis adları farklıysa mevcut isimlendirmeyle uyumlu çalış.

Her servis için:
- Dockerfile
- Healthcheck
- Environment configuration
- Restart policy
- Network configuration
- Persistent volumes

oluştur.

PostgreSQL verileri persistent volume üzerinde saklanmalıdır.

Veritabanı migration işlemleri kontrollü bir şekilde çalıştırılmalıdır.

Docker Compose üzerinden sistemi başlatabilmeliyim.

Mevcut Java uygulamalarını, Chaos Application'ı veya Dynatrace entegrasyonlarını bozma.

# 11. PROJECT STRUCTURE

Önerilen yapı:

sre-platform/
- frontend/
  - src/
    - components/
    - pages/
    - hooks/
    - services/
    - types/
    - layouts/
    - features/
  - Dockerfile
  - package.json
- backend/
  - api/
    - routes/
    - schemas/
    - dependencies/
  - agent/
    - graph/
    - memory/
    - tools/
    - prompts/
  - database/
    - models/
    - repositories/
    - migrations/
  - services/
    - investigation/
    - incident/
    - rca/
    - knowledge/
  - core/
  - tests/
  - Dockerfile
- docker-compose.yml
- .env.example
- README.md

Mevcut repository yapısına göre bu organizasyonu uyarlayabilirsin.

Gereksiz taşıma veya kapsamlı refactoring yapma.

# 12. SECURITY & RELIABILITY

Uygun authentication ve authorization mimarisi oluştur.

Birden fazla kullanıcı desteklenecekse kayıtların kullanıcı veya tenant bazında ayrılmasını sağla.

CORS ayarlarını güvenli yapılandır.

Database connection pool kullan.

HTTP timeout, retry ve error handling uygula.

Agent'a sınırsız tool execution yetkisi verme.

Agent recursion ve execution limitlerini yapılandırılabilir yap.

MCP tool output'larını güvenilmeyen veri olarak değerlendir.

SQL injection, prompt injection ve hassas veri sızıntılarına karşı koruma uygula.

Structured logging ve correlation ID desteği ekle.

Gerekli backend unit testlerini ve integration testlerini yaz.

Frontend için temel component testleri ekle.

# 13. DEVELOPMENT STRATEGY

Çalışmayı aşamalara böl.

**Phase 1 — Repository Analysis**
- Mevcut mimariyi analiz et.
- Çalışan bileşenleri belirle.
- Eksik bileşenleri tespit et.
- Uygulama planı oluştur.

**Phase 2 — PostgreSQL Persistence**
- Database models
- SQLAlchemy
- Alembic migrations
- Repositories
- Connection management

**Phase 3 — Agent Memory**
- LangGraph PostgreSQL Checkpointer
- Persistent conversation memory
- Investigation history
- Historical incident lookup

**Phase 4 — Backend APIs**
- FastAPI endpoints
- Service layer
- SSE streaming
- Database integration
- Error handling

**Phase 5 — React Frontend**
- Layout
- Dashboard
- AI Investigation
- Agent Activity
- Investigation History
- Incident Management
- RCA Reports
- Knowledge Base

**Phase 6 — Advanced Capabilities**
- Historical RCA comparison
- Evidence correlation
- Feedback system
- Known Issues
- Runbook recommendations
- Scheduled health check foundation

**Phase 7 — Docker Integration & Testing**
- Docker Compose
- Database migration
- Healthchecks
- API tests
- Frontend integration
- End-to-end validation

# 14. ACCEPTANCE CRITERIA

Geliştirme sonunda aşağıdaki senaryolar çalışmalıdır:

1. Kullanıcı React frontend üzerinden SRE Agent'a soru sorabilmeli.
2. Agent mevcut Dynatrace MCP entegrasyonunu kullanabilmeli.
3. Agent yanıtları streaming olarak görüntülenebilmeli.
4. Konuşmalar PostgreSQL'e kaydedilmeli.
5. Önceki konuşmalar tekrar açılabilmeli.
6. Investigation kayıtları PostgreSQL'de tutulmalı.
7. Agent tool execution geçmişi görüntülenebilmeli.
8. Evidence kayıtları ilgili investigation ile ilişkilendirilmeli.
9. Incident kayıtları oluşturulabilmeli ve durumları güncellenebilmeli.
10. RCA raporları kaydedilip tekrar görüntülenebilmeli.
11. Agent geçmiş incident'ları sorgulayabilmeli.
12. Kullanıcı Agent analizlerini değerlendirebilmeli.
13. Frontend Docker üzerinde çalışmalı.
14. Backend Docker üzerinde çalışmalı.
15. SRE PostgreSQL persistent volume ile çalışmalı.
16. Sistemin restart edilmesi geçmiş verileri silmemeli.
17. Mevcut Java uygulamaları ve Chaos Application çalışmaya devam etmeli.
18. Dynatrace verisi mevcut değilse sistem bunu açıkça belirtmeli, sahte verileri gerçek veri gibi göstermemeli.

# 15. IMPORTANT IMPLEMENTATION RULES

- Mevcut kodları analiz etmeden yeni mimari kurma.
- Var olan ve çalışan Agent'ı sıfırdan yazma.
- Mevcut Dynatrace MCP entegrasyonunu koru.
- Kullanılan LangChain/LangGraph sürümleriyle uyumlu çözüm geliştir.
- Dependency sürümlerini doğrula.
- Gereksiz bağımlılık ekleme.
- Frontend ve backend arasında net sorumluluk ayrımı yap.
- SOLID ve Clean Architecture prensiplerini pragmatik biçimde uygula.
- Kritik iş kurallarını frontend'e taşıma.
- Agent tool execution ve memory yönetimini backend tarafında tut.
- Demo amaçlı mock veri gerekiyorsa açıkça etiketle ve gerçek veri akışından ayır.
- Üretim ortamı varmış gibi yanıltıcı sonuçlar oluşturma.
- README ve .env.example dosyalarını güncelle.
- Her aşamada ilgili testleri çalıştır ve sonuçlarını raporla.
- Başarısız testleri gizleme.
- Tamamlanmayan özellikleri tamamlanmış gibi sunma.

# 16. FINAL DELIVERABLES

Çalışma tamamlandığında:

1. Güncellenmiş proje mimarisını göster.
2. Eklenen ve değiştirilen dosyaları özetle.
3. PostgreSQL şemasını açıkla.
4. API endpoint'lerini listele.
5. Agent Memory mekanizmasını açıkla.
6. Docker Compose başlatma komutlarını ver.
7. Migration komutlarını açıkla.
8. Frontend'e nasıl erişeceğimi göster.
9. Test sonuçlarını paylaş.
10. Mevcut sistemden kalan eksiklikleri ve riskleri belirt.

**Öncelik:** Önce çalışan ve kalıcı bir temel oluştur. Frontend tasarımı etkileyici olmalı fakat gerçek API, Agent ve PostgreSQL entegrasyonlarından bağımsız bir görsel prototip olarak kalmamalıdır.

Şimdi repository'yi analiz ederek başla. Mevcut yapıyı koruyan geliştirme planını oluştur ve ardından plan doğrultusunda implementasyonu gerçekleştir.
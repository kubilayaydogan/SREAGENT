# Sistem ve Agent Çalışma Akışları

Bu doküman platformda gerçekten var olan akışları diyagramlarla anlatır. Her akış için tetikleyici, ön koşul, katılan
bileşenler, başarılı ve hatalı yollar ve çıktı verilir.

Bileşenlerin tanımı için: [Teknik Mimari](TECHNICAL_ARCHITECTURE.md) ve [Agent Mimarisi](AGENT_ARCHITECTURE.md).

[← Dokümantasyon merkezine dön](../README.md)

## İçindekiler

- [1. Kullanıcı sorusundan yanıta](#1-kullanıcı-sorusundan-yanıta)
- [2. Zamanlanmış çalışma: watch ve health check](#2-zamanlanmış-çalışma-watch-ve-health-check)
- [3. Dynatrace MCP veri toplama ve korelasyon](#3-dynatrace-mcp-veri-toplama-ve-korelasyon)
- [4. Risk score ve RCA üretimi](#4-risk-score-ve-rca-üretimi)
- [5. Graph node geçişleri ve state değişimi](#5-graph-node-geçişleri-ve-state-değişimi)
- [6. LLM retry, fallback ve hata akışı](#6-llm-retry-fallback-ve-hata-akışı)
- [7. Guardrail ve middleware kararları](#7-guardrail-ve-middleware-kararları)
- [8. Memory retrieval ve persistence](#8-memory-retrieval-ve-persistence)
- [9. Agent Builder: oluşturma, çalıştırma, import ve export](#9-agent-builder-oluşturma-çalıştırma-import-ve-export)
- [10. Chaos senaryosundan agent analizine](#10-chaos-senaryosundan-agent-analizine)
- [11. Onay akışı (human-in-the-loop)](#11-onay-akışı-human-in-the-loop)

## 1. Kullanıcı sorusundan yanıta

| | |
|---|---|
| **Tetikleyici** | Kullanıcı AI Investigation ekranında bir soru gönderir. |
| **Ön koşul** | Oturum açık, `agent:run` yetkisi var, agent etkin, Dynatrace MCP yapılandırılmış. |
| **Bileşenler** | Arayüz, backend, `ChatOrchestrator`, analiz graph'ı, `McpGateway`, `Llm`, PostgreSQL. |
| **Çıktı** | Sohbet yanıtı, investigation kaydı, evidence kayıtları, izleme kayıtları. |

```mermaid
sequenceDiagram
    autonumber
    actor U as Kullanıcı
    participant UI as Arayüz
    participant API as Backend
    participant CH as ChatOrchestrator
    participant AG as Analiz graph'ı
    participant MCP as Dynatrace MCP
    participant LLM as Claude
    participant DB as PostgreSQL

    U->>UI: Soru yazar
    UI->>API: POST /api/agent/chat
    API->>API: Yetki, hız sınırı, guardrail (girdi)
    API-->>UI: 202 run_id
    UI->>API: SSE akışına bağlan
    API->>CH: Turu başlat
    CH->>LLM: route: soruyu yapılandır
    LLM-->>CH: AnalysisRequest (niyet, uygulama, zaman)
    CH->>AG: investigate
    AG->>MCP: Signal query'leri (execute-dql)
    MCP-->>AG: Sonuçlar
    AG->>AG: Feature, risk score, attribution
    AG->>LLM: explain: bulguları açıkla
    LLM-->>AG: Açıklama
    AG->>DB: Investigation ve evidence kaydı
    CH->>DB: recall: benzer kayıtlar, memory
    CH->>LLM: respond: yanıtı üret (streaming)
    LLM-->>UI: Token'lar (SSE)
    CH->>DB: Mesajı ve checkpoint'i kaydet
    API-->>UI: done
```

**Başarılı yol:** Yanıt, altında risk score, olası mechanism, data coverage ve incelenen pencereyi gösteren bir
investigation kartıyla gelir. Kullanıcı karttan adımları, evidence'ı, MCP çağrılarını ve Agent Trace'i açabilir.

**Hatalı yollar:**

| Durum | Sonuç |
|---|---|
| Niyet `smalltalk` | Analiz çalışmaz; doğrudan yanıt verilir. |
| Niyet `history` ya da `followup` | Yeni analiz çalışmaz; mevcut kayıtlardan yanıt verilir. |
| Guardrail girdiyi engelledi | Akış doğrudan yanıta gider; engel nedeni bildirilir. |
| MCP'ye ulaşılamadı | Investigation `partial` olur; yanıt eksik veriyi açıkça yazar. |
| LLM'e ulaşılamadı | Soru sezgisel ayrıştırıcıyla yorumlanır, açıklama deterministik metinle üretilir. |
| Kullanıcı iptal etti ya da süre doldu | Run `cancelled` / `timeout` olarak kapanır; o ana kadarki adımlar kayıtlıdır. |
| Hız ya da eşzamanlılık sınırı | API `429` döner; run başlamaz. |
| Aynı konuşmada süren bir tur var | API `409` döner. |

## 2. Zamanlanmış çalışma: watch ve health check

| | |
|---|---|
| **Tetikleyici** | Worker'daki aralık tabanlı işler: `watch` (60 s), `health-check` (15 dk), custom agent schedule'ları. |
| **Ön koşul** | `sre-worker` çalışıyor, iş Settings › Scheduling'de etkin. |
| **Bileşenler** | Worker, watch durum makinesi, analiz graph'ı, MCP, PostgreSQL, isteğe bağlı Slack. |
| **Çıktı** | Watch olayları, incident, investigation, isteğe bağlı bildirim. |

### Watch durum makinesi

Normal watch döngüsü LLM çağırmaz. Tamamlanmış 1 dakikalık pencereleri, önceki 15 pencerenin medyanıyla karşılaştırır.

```mermaid
stateDiagram-v2
    state "Normal" as normal
    state "Şüpheli" as suspect
    state "Incident açık" as open
    state "Kapanıyor" as closing
    state "Cooldown" as cooldown
    state "Veri yok" as gap
    [*] --> normal
    normal --> suspect: Pencere open threshold'unu aştı
    suspect --> normal: Ara değer ya da temiz pencere, sayaç sıfırlanır
    suspect --> open: Art arda 3 pencere aşım
    open --> open: Yeni anahtarlar aynı incident'a eklenir
    open --> closing: Pencere clear threshold'unun altında
    closing --> open: Değer yeniden yükseldi
    closing --> cooldown: Art arda 3 temiz pencere
    cooldown --> normal: 10 dakika sonra
    normal --> gap: Art arda 5 pencere veri yok
    gap --> normal: Veri geri geldi
```

İzlenen değerler ve threshold'lar (`open` / `clear`): latency oranı 2,0 / 1,3; hata oranı 0,02 / 0,005; Connection Pool
doluluğu 0,9 / 0,7; lock bekleyen 1,0 / 0,2; veritabanı bağlantı oranı 0,85 / 0,7; host CPU %85 / %70. Açma ve kapama
threshold'larının farklı olması (hysteresis) değerin sınırda gidip gelmesi durumunda incident'ın sürekli açılıp
kapanmasını önler.

Veri gelmeyen pencere sıfır sayılmaz: sayaçlar değişmez. Uzun süren veri boşluğu ayrı bir "telemetri eksik" olayıdır.

### Incident açıldığında

```mermaid
sequenceDiagram
    participant W as Worker (watch)
    participant MCP as Dynatrace MCP
    participant DB as PostgreSQL
    participant AG as Analiz graph'ı
    participant LLM as Claude
    participant SL as Slack (isteğe bağlı)

    loop Her 60 saniye
        W->>MCP: Watch signal'ları
        MCP-->>W: Son pencereler
        W->>DB: Pencere ve sayaç durumu
    end
    Note over W: 3 ardışık pencere threshold üstünde
    W->>DB: Incident aç
    W->>AG: Tam analiz (son 10 dakika)
    AG->>MCP: Tüm signal kataloğu
    AG->>LLM: Açıklama (ayar açıksa)
    AG->>DB: Investigation, incident'a bağlanır
    W->>SL: Risk threshold'u aşıldıysa bildirim
```

### Health check ve custom agent schedule'ı

- **Health check** her 15 dakikada tüm servisler için tam analiz çalıştırır ve sonucu investigation olarak kaydeder
  (`SRE_HEALTH_CHECK_MINUTES`, `SRE_HEALTH_CHECK_WINDOW_MINUTES`).
- **Custom agent schedule'ı**, yayımlanmış ve "Zamanlanmış" modu açık bir custom agent'ı belirlenen aralıkta
  çalıştırır; sonuç `agent_runs` kaydıdır.

**Hatalı yollar:** Bir iş çalışırken ikinci kopyası başlamaz; kaçan periyotlar biriktirilmez. İş hata verirse hata
`job_runs` kaydına yazılır ve bir sonraki periyotta yeniden denenir. Worker dururken hiçbir zamanlanmış iş çalışmaz;
soru-cevap etkilenmez.

Kaynak: [`watch.py`](../sre-agent/src/airlab_agent/watch.py),
[`worker.py`](../sre-platform/backend/sre_platform/worker.py),
[`watch_pg.py`](../sre-platform/backend/sre_platform/services/watch_pg.py).

## 3. Dynatrace MCP veri toplama ve korelasyon

| | |
|---|---|
| **Tetikleyici** | Analiz graph'ının `collect` ve `extra_evidence` node'ları. |
| **Ön koşul** | MCP adresi ve token tanımlı; tool tanımı onaylı. |
| **Bileşenler** | Signal kataloğu, middleware zinciri, `McpGateway`, Dynatrace. |
| **Çıktı** | Signal başına sonuç ve veri durumu; evidence kayıtları. |

```mermaid
flowchart TD
    A["Signal kataloğu:<br/>pencere ve baseline için DQL üret"] --> B{"Query bütçesi<br/>kaldı mı?"}
    B -->|"Hayır"| SK["SKIPPED_BUDGET"]
    B -->|"Evet"| C["Middleware: allowlist,<br/>DQL doğrulama, limit"]
    C -->|"Reddedildi"| ER["ERROR<br/>(çağrı yapılmaz)"]
    C -->|"İzin verildi"| D["McpGateway: execute-dql"]
    D --> E{"Yanıt"}
    E -->|"Geçici hata"| R{"Retry hakkı<br/>var mı?"}
    R -->|"Evet"| D
    R -->|"Hayır"| UN["UNAVAILABLE / ERROR"]
    E -->|"Yetki hatası"| PE["PERMISSION"]
    E -->|"Satır yok"| EM["EMPTY"]
    E -->|"Veri eski"| ST["STALE"]
    E -->|"Veri var"| OK["OK"]
    SK --> F["Evidence kaydı"]
    ER --> F
    UN --> F
    PE --> F
    EM --> F
    ST --> F
    OK --> F
    F --> G["Feature çıkarımı:<br/>pencere ve baseline karşılaştırması"]
    G --> H["Korelasyon: aynı pencerede<br/>uygulama, veritabanı ve host signal'ları"]
```

Korelasyon zaman hizalamasına dayanır: uygulama metrikleri (latency, hata, Connection Pool), veritabanı metrikleri
(oturumlar, lock'lar, statement'lar) ve host metrikleri (CPU) aynı pencere ve aynı baseline üzerinden karşılaştırılır.
Lock edge kayıtları hangi uygulamanın hangisini beklettiğini doğrudan gösterir.

Ek kanıt turu yalnız gerektiğinde çalışır: `row_lock` bulunursa lock edge ayrıntısı; `db_cpu`,
`global_connection_pressure`, `db_memory_pressure` ya da bir PostgreSQL workload mechanism'i bulunursa en pahalı
statement'lar toplanır.

## 4. Risk score ve RCA üretimi

| | |
|---|---|
| **Tetikleyici** | Risk score: her analizde otomatik. RCA: kullanıcı investigation'da "RCA üret" der. |
| **Ön koşul** | RCA için tamamlanmış bir investigation ve `incident:write` yetkisi. |
| **Bileşenler** | `features`, `scoring`, `attribution`, `rca_service`, isteğe bağlı LLM. |
| **Çıktı** | Uygulama başına skor ve bant; mechanism hipotezi; RCA raporu. |

```mermaid
flowchart TD
    EV["Evidence<br/>(signal sonuçları + veri durumu)"] --> FE["Feature çıkarımı<br/>pencere / baseline"]
    FE --> Q["Veri kalitesi<br/>data coverage"]
    FE --> SC["Bileşen skorları:<br/>latency, errors, saturation,<br/>lock, resource"]
    Q --> CV{"Ölçülebilen ağırlık<br/>en az %60 mı?"}
    SC --> CV
    CV -->|"Hayır"| NS["Skor yok (null)<br/>'yetersiz veri'"]
    CV -->|"Evet"| RS["Risk score 0–100<br/>ve bant"]
    FE --> AT["Mechanism tespiti<br/>(kural tabanlı)"]
    AT --> SRC["Source ve etkilenen<br/>uygulamalar, confidence"]
    AT --> ALT["Alternatif açıklamalar"]
    RS --> REP["Investigation raporu"]
    NS --> REP
    SRC --> REP
    ALT --> REP
    REP --> X{"LLM kullanılabilir mi?"}
    X -->|"Evet"| LE["LLM açıklaması"]
    X -->|"Hayır"| DE["Deterministik açıklama"]
    LE --> RCA["RCA raporu<br/>(kullanıcı isteğiyle)"]
    DE --> RCA
```

RCA raporunun bölümleri (bulgular, evidence, incident zaman çizelgesi, bilgi bankası önerileri) deterministik olarak
doldurulur. LLM varsa yalnız "Yönetici özeti" paragrafını yazar. Root Cause her zaman **hipotez** olarak sunulur;
confidence ve belirsizlik ayrı bir bölümdür.

**Hatalı yollar:** Veri yetersizse skor üretilmez ve bu açıkça yazılır. Hiçbir mechanism kuralı tutmazsa etki
raporlanır, neden "açıklanamayan" olarak bırakılır; agent bir neden uydurmaz.

## 5. Graph node geçişleri ve state değişimi

| | |
|---|---|
| **Tetikleyici** | Her analiz çalışması. |
| **Bileşenler** | Analiz graph'ı, guardrails (`guard_node`), checkpointer, `TraceRecorder`. |
| **Çıktı** | Her node'dan sonra güncellenmiş state, checkpoint ve trace event'i. |

```mermaid
sequenceDiagram
    participant G as Graph
    participant GR as Guardrails
    participant N as Node
    participant CP as Checkpointer
    participant TR as Agent Trace

    loop plan, collect, analyze, extra_evidence, explain, finalize
        G->>GR: node_start değerlendirmesi
        G->>TR: node başladı
        G->>N: Node'u çalıştır (mevcut state)
        N-->>G: State güncellemesi
        G->>GR: node_end değerlendirmesi
        G->>CP: Checkpoint yaz
        G->>TR: node bitti + state farkı
    end
```

Node başına state'e eklenenler:

| Node | State'e yazdıkları |
|---|---|
| `plan` | `start`, `end`, `baseline_start`, `baseline_end`, `windows`, `notes` |
| `collect` | `results`, `evidence`, `budget`, `errors` |
| `analyze` | `quality`, `features_summary`, `scores`, `attribution`, `alternatives`, `status` |
| `extra_evidence` | `extra`, `iterations`, güncellenmiş `evidence` |
| `explain` | `explanation`, `explanation_source` |
| `finalize` | `status` (`completed` / `partial`), rapor |

`analyze` node'undan sonra `needs_extra` fonksiyonu karar verir: ek kanıt gerekiyorsa ve tur ile bütçe sınırı
aşılmadıysa `extra_evidence`, aksi halde `explain`.

**Hatalı yol:** Backend çalışma sırasında durursa son checkpoint korunur. Sohbet turu "resume" ile kaldığı node'dan
sürdürülebilir.

## 6. LLM retry, fallback ve hata akışı

| | |
|---|---|
| **Tetikleyici** | Her LLM çağrısı (`route`, `explain`, `respond`, RCA özeti, custom agent LLM node'u). |
| **Bileşenler** | `Llm`, `RunBudget`, middleware zinciri, Anthropic SDK. |
| **Çıktı** | Model yanıtı ya da deterministik yedek metin. |

```mermaid
flowchart TD
    S["LLM çağrısı istendi"] --> K{"LLM etkin mi?<br/>(anahtar var, kapatılmamış)"}
    K -->|"Hayır"| DET["Deterministik yol"]
    K -->|"Evet"| B{"Çağrı / token<br/>bütçesi var mı?"}
    B -->|"Hayır"| DET
    B -->|"Evet"| MW["Middleware: redaction,<br/>call limit"]
    MW --> GI{"Guardrail<br/>(llm_request)"}
    GI -->|"Engelledi"| DET
    GI -->|"İzin verdi"| LLMAPI["Anthropic API çağrısı"]
    LLMAPI --> R{"Sonuç"}
    R -->|"Geçici hata / timeout"| RT{"SDK retry hakkı<br/>var mı?"}
    RT -->|"Evet"| LLMAPI
    RT -->|"Hayır"| DET
    R -->|"Refusal"| FB["Sunucu tarafı fallback<br/>(aynı provider)"]
    FB --> GO
    R -->|"Yanıt"| GO{"Guardrail<br/>(llm_response)"}
    GO -->|"Engelledi"| DET
    GO -->|"Maskeledi / temizledi"| OUT["Yanıt (düzeltilmiş)"]
    GO -->|"İzin verdi"| OUT2["Yanıt"]
    DET --> FIN["Analiz devam eder,<br/>açıklama kaynağı: deterministic"]
```

- **Retry** Anthropic SDK tarafından yapılır (varsayılan 2). Platform ikinci bir retry katmanı eklemez.
- **Fallback** yalnız Anthropic'in sunucu tarafı refusal fallback'idir.
- **Provider failover yoktur.** Başka bir provider'a ya da modele geçilmez.
- **Deterministik yol** her zaman vardır: soru yorumlama için sezgisel ayrıştırıcı, açıklama için kural tabanlı metin.

Yanıtın altında açıklamanın kaynağı (`Claude` ya da deterministik) her zaman gösterilir.

## 7. Guardrail ve middleware kararları

| | |
|---|---|
| **Tetikleyici** | Her agent çalışmasının girdisi, her LLM ve MCP çağrısı, her memory işlemi, son çıktı. |
| **Bileşenler** | Middleware zinciri, guardrails motoru, policy deposu. |
| **Çıktı** | Karar kaydı; gerekirse engellenen, maskelenen ya da temizlenen içerik. |

```mermaid
flowchart TD
    C["Çağrı ya da içerik"] --> M["Middleware zinciri<br/>(sıra numarasına göre)"]
    M --> M1{"Zorunlu kontroller:<br/>allowlist, parametre,<br/>limit"}
    M1 -->|"Başarısız"| BLK["Çağrı engellenir<br/>(MiddlewareBlocked)"]
    M1 -->|"Geçti"| P["Guardrails: aşamaya uyan<br/>policy'leri bul"]
    P --> KS{"Kill switch<br/>açık mı?"}
    KS -->|"Evet"| TERM["Çalışma başlamaz"]
    KS -->|"Hayır"| EV["Policy'leri değerlendir"]
    EV --> MODE{"Policy modu"}
    MODE -->|"disabled"| SKIP["Atlanır"]
    MODE -->|"monitor"| LOG["Yalnız kaydedilir"]
    MODE -->|"enforce"| DEC["Deny-overrides:<br/>en kısıtlayıcı karar"]
    DEC -->|"allow / warn"| GO["Devam"]
    DEC -->|"redact / sanitize"| FIX["İçerik düzeltilir, devam"]
    DEC -->|"block / terminate"| STOP["Engellenir"]
    DEC -->|"require_approval"| APP["İnsan onayı beklenir"]
    LOG --> GO
    SKIP --> GO
    STOP --> REC["Değerlendirme ve ihlal kaydı"]
    FIX --> REC
    APP --> REC
    GO --> REC
```

**Örnekler:**

| Durum | Karar |
|---|---|
| LLM'e gidecek mesajda bir secret kalıbı | `redaction` middleware'i maskeler; çağrı devam eder |
| Yazma yetenekli bir tool çağrısı | `tool_allowlist` engeller |
| DQL'de okuma dışı komut | `tool_param_validation` engeller; çağrı Dynatrace'e gitmez |
| Yanıtta evidence kimliği olmayan Root Cause iddiası | Guardrail çıktıyı temizler (`sanitize`) |
| Kullanıcı girdisinde prompt injection kalıbı | Guardrail engeller; yanıt engeli bildirir |

Zorunlu middleware'ler ve zorunlu policy'ler arayüzden kapatılamaz. Bir policy'nin etkisini canlıya almadan görmek
için Guardrails › **Policy Tester** ve Agent Middleware › **Dry-run** kullanılır.

## 8. Memory retrieval ve persistence

| | |
|---|---|
| **Tetikleyici** | Sohbet turunun `recall` node'u (okuma); tur ve investigation sonu (yazma). |
| **Bileşenler** | `ChatOrchestrator`, memory yöneticisi, checkpointer, guardrails. |
| **Çıktı** | Yanıta eklenen geçmiş bağlamı; güncellenen working ve episodic memory. |

```mermaid
sequenceDiagram
    participant CH as ChatOrchestrator
    participant GR as Guardrails
    participant MM as Memory yöneticisi
    participant DB as PostgreSQL

    Note over CH: Tur başı
    CH->>DB: Checkpoint'i yükle (thread_id = konuşma)
    DB-->>CH: Son mesajlar, focus, digests

    Note over CH: recall
    CH->>GR: memory_read değerlendirmesi
    CH->>MM: Benzer kayıtları getir
    MM->>DB: Episode'lar (parmak izi benzerliği)
    MM->>DB: Investigation, runbook, bilinen sorun
    DB-->>MM: Adaylar
    MM-->>CH: Sıralanmış kayıtlar + retrieval kaydı

    Note over CH: Tur sonu
    CH->>GR: memory_write değerlendirmesi
    CH->>DB: Mesaj, focus, digests (checkpoint)
    CH->>DB: Episode: belirti parmak izi, hipotez, sonuç
```

- **Okuma:** Benzerlik, belirti parmak izlerinin Jaccard benzerliğiyle hesaplanır; embedding yoktur. Getirilen her
  kayıt, neden seçildiğiyle birlikte **Agent Memory › Memory Retrieval** sekmesinde görünür.
- **Yazma:** Working memory her turda checkpoint'e yazılır. Episodic memory investigation tamamlandığında güncellenir.
- **Kullanım:** Getirilen kayıtlar yanıta ipucu olarak girer; güncel risk score'u ve mechanism'i değiştirmez.

**Hatalı yollar:** Guardrail hassas içerikli bir memory yazımını engelleyebilir ya da maskeleyebilir. Memory
retrieval başarısız olursa yanıt geçmiş bağlamı olmadan üretilir; analiz sonucu etkilenmez.

## 9. Agent Builder: oluşturma, çalıştırma, import ve export

| | |
|---|---|
| **Tetikleyici** | Kullanıcı Agent Builder'da bir agent oluşturur, test eder, yayımlar ya da içe / dışa aktarır. |
| **Ön koşul** | Oluşturma ve test için `agent:build`; yayım ve arşiv için `agent:publish`; çalıştırma için `agent:run`. |
| **Bileşenler** | Arayüz (canvas), builder servisi, doğrulayıcı, builder motoru, LLM, MCP. |
| **Çıktı** | Sürümlenmiş agent tanımı; `agent_runs` kayıtları. |

```mermaid
flowchart TD
    NEW["Yeni agent"] --> D["Taslak<br/>(canvas'ta düzenle)"]
    IMP["JSON içe aktar"] --> PV["Önizleme ve doğrulama"] --> D
    CP["Mevcut agent'ı kopyala"] --> D
    D --> V{"Doğrula"}
    V -->|"Sorun var"| D
    V -->|"Geçti"| T["Test çalıştırması<br/>(taslak üzerinde)"]
    T --> D
    V -->|"Geçti"| PUB["Yayımla → sürüm N"]
    PUB --> USE{"Kullanım"}
    USE --> QA["AI Investigation'da<br/>agent seçimi"]
    USE --> SCH["Schedule"]
    USE --> EXP["JSON dışa aktar"]
    PUB --> ACT["Eski sürümü aktifleştir"]
    PUB --> ARC["Arşivle"]
```

### Çalıştırma

```mermaid
sequenceDiagram
    participant U as Kullanıcı / schedule
    participant BE as Builder motoru
    participant LG as LangGraph
    participant LLM as Claude
    participant MCP as Dynatrace MCP
    participant DB as PostgreSQL

    U->>BE: Çalıştır (agent, sürüm, girdi)
    BE->>DB: Yayımlanmış tanımı oku
    BE->>BE: Tanımı StateGraph'a derle
    BE->>LG: Graph'ı başlat
    loop Workflow node'ları
        alt LLM node'u
            LG->>LLM: Middleware ve guardrails üzerinden çağrı
        else MCP Tool node'u
            LG->>MCP: Salt okunur DQL
        else Koşul node'u
            LG->>LG: true / false dalını seç
        else Memory Retrieval node'u
            LG->>DB: Kayıtları getir
        end
    end
    LG-->>BE: Sonuç node'unun çıktısı
    BE->>DB: agent_runs, Agent Trace, LLM usage
    BE-->>U: Yanıt
```

- Tanım çalışma başında belleğe alınır; çalışma sürerken yapılan düzenleme ya da yeni yayım onu etkilemez.
- **Export** dosyası secret içermez: connector ve model referansları ad olarak taşınır. **Import** önce önizlenir;
  hedef ortamda bulunmayan connector ya da model sorun olarak listelenir.
- Custom agent risk score üretmez ve investigation kaydı oluşturmaz.

**Hatalı yollar:** Doğrulama sorunları yayımı engeller. Çalışma sırasında bir node hata verirse çalışma `failed`
olur, hata `agent_runs` kaydına yazılır ve çalışma yeniden denenebilir. Yazma yetenekli tool içeren bir tanım policy
tarafından reddedilir.

Ayrıntı: [agent-builder.md](platform/agent-builder.md).

## 10. Chaos senaryosundan agent analizine

| | |
|---|---|
| **Tetikleyici** | Operatör Chaos Controller'da bir senaryo başlatır. |
| **Ön koşul** | Laboratuvar çalışıyor; OneAgent ve `pg-lock-collector` Dynatrace'e veri gönderiyor. |
| **Bileşenler** | Chaos Controller, laboratuvar servisleri, laboratuvar PostgreSQL'i, Dynatrace, platform. |
| **Çıktı** | Chaos raporu (beklenen ve gözlenen etki) ve agent'ın bağımsız investigation'ı. |

```mermaid
sequenceDiagram
    actor OP as Operatör
    participant CC as Chaos Controller
    participant APP as Laboratuvar servisleri
    participant PG as Laboratuvar PostgreSQL
    participant DT as Dynatrace
    participant AG as SRE Agent (platform)

    OP->>CC: Senaryo başlat (ör. S03, yoğunluk)
    CC->>CC: Doğrula, baseline ölç
    CC->>APP: Workload başlat (uygulamanın kendi DB rolüyle)
    APP->>PG: Yük (lock, CPU, bağlantı...)
    par Telemetri
        APP->>DT: OneAgent: latency, hata, pool, JVM
        PG->>DT: Collector: lock, statement, oturum
    end
    loop Deney süresince
        CC->>APP: Metrikleri ölç, yükü ayarla
        CC->>CC: Güvenlik sınırlarını kontrol et
    end
    CC->>APP: Cleanup
    CC->>CC: Recovery'yi doğrula, rapor yaz
    Note over AG: Chaos Controller ile agent arasında bağlantı yoktur
    alt Watch
        AG->>DT: Periyodik query'ler → threshold aşımı → incident
    else Kullanıcı sorusu
        OP->>AG: "Son 15 dakikada ne oldu?"
        AG->>DT: Signal kataloğu
    end
    AG-->>OP: Mechanism hipotezi, source uygulama, evidence
```

Önemli nokta: agent Chaos Controller'dan **haber almaz**. Hangi senaryonun çalıştığını bilmez; yalnız Dynatrace'teki
telemetriyi görür. Bu, agent'ın değerlendirmesini "kör" kılar: senaryo adı agent'a sızmaz.

Chaos Controller deney durumları: `CREATED`, `VALIDATING`, `BASELINING`, `RAMPING_UP`, `RUNNING`, `RECOVERING` ve
sonuç durumları `COMPLETED`, `FAILED`, `ABORTED`, `TIMED_OUT`. Aynı anda tek deney çalışır.

**Hatalı yollar:** Güvenlik sınırı aşılırsa (bütçe, disk, veritabanı bağlantısı, erişilemezlik) deney kesilir ve
cleanup çalışır. Agent tarafında, katalogda karşılığı olmayan bir arıza türünde etki raporlanır ama mechanism
"açıklanamayan" kalır.

Kör değerlendirme sonuçları ve bilinen eksikler: [chaos-v2-agent-eval.md](lab/chaos-v2-agent-eval.md). Senaryo
listesi ve API: [chaos-v2-architecture.md](lab/chaos-v2-architecture.md).

## 11. Onay akışı (human-in-the-loop)

| | |
|---|---|
| **Tetikleyici** | Bir kullanıcı salt okunur olmayan bir aksiyon için onay talebi açar. |
| **Ön koşul** | Talep için `approval:request`; karar için `approval:decide`. |
| **Çıktı** | Onaylanan ya da reddedilen talep; audit kaydı. |

```mermaid
sequenceDiagram
    actor S as SRE
    participant P as Platform
    actor A as Yönetici

    S->>P: Onay talebi (aksiyon, gerekçe, ilgili kayıt)
    P->>P: Talebi "bekliyor" olarak kaydet
    A->>P: Onaylar ekranında incele
    alt Onay
        A->>P: Onayla
    else Ret
        A->>P: Reddet (gerekçe)
    end
    P->>P: Karar ve audit kaydı
    P-->>S: Talebin durumu güncellenir
```

- **Four-eyes:** Talebi açan kişi kendi talebini onaylayamaz.
- **Süre:** Karar verilmeyen talep 24 saat sonra zaman aşımına uğrar.

Platform onaylanan aksiyonu **kendisi yürütmez**. Onay, kararın kim tarafından ve ne zaman verildiğinin kaydıdır;
aksiyonu operatör kendi araçlarıyla uygular. Agent hiçbir koşulda izlenen sistemi değiştirmez.

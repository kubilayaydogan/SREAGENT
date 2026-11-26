# Agent Builder

Platform › **Agent Builder** (`/agent-builder`) özel agent'ları görsel workflow olarak oluşturur. Kullanıcı taslağı test eder, değişmez sürüm olarak yayımlar, sonra Soru-Cevap'ta ve schedule'larda çalıştırır. Gömülü SRE Agent listede **sistem agent'ı** olarak görünür. Workflow'u platform kodundadır; bu yüzden kopyalanamaz ve dışa aktarılamaz.

## Kavramlar

| Kavram | Açıklama |
|---|---|
| Taslak | Düzenlenebilir tanım (`sre.agents.draft`, `draft_revision`). Eşzamanlı kayıtlar revizyon kontrolüyle yakalanır (409). |
| Sürüm | Yayımlama değişmez bir kopya oluşturur (`sre.agent_versions`, checksum). Normal çalıştırmalar yalnız `active_version`'ı kullanır. Önceki sürüm incelenebilir ve tekrar aktif edilebilir. |
| Run kaydı | Her soru-cevap, zamanlanmış ve test çalıştırması `sre.agent_runs`'a yazılır: agent, sürüm, mod, node başına gerçek model, node log'u, token kullanımı, çıktı ve hata. |
| Sürüm sabitleme | Agent ve aktif sürüm koşu başında çözülür. Başlamış bir run, yeni sürüm yayımlansa da kendi sürümüyle biter. Retry özgün sürümü kullanır. |

## Workflow

Node'lar: Başlangıç, LLM, MCP Tool, Koşul (true/false), Memory Retrieval, Structured Output (json_schema), Sonuç. İki node paletinde nedeniyle birlikte "kullanılamıyor" olarak görünür:
- insan onayı: yazma yetenekli tool için onaylı yürütme yolu yok;
- paralel dal: ilk sürümde workflow sıralıdır.

Şablonlarda kullanılabilenler: `{{input}}`, `{{now}}`, `{{agent}}`, `{{mode}}`, başlangıç değişkenleri ve önceki node çıktıları (`{{var.path}}`). Metin olmayan değerler modele ayrı veri bloğu olarak gider; talimat olarak yorumlanmaz.

Doğrulama backend'de kaydet, test ve yayımla adımlarında çalışır; hata ilgili node üzerinde gösterilir. Kontroller:
- şema;
- tek başlangıç node'u, en az bir sonuç node'u;
- kopuk, kendine ya da yinelenen bağlantı;
- dal kuralları: her node tek çıkış, koşul true + false;
- döngü (`cycle`) ve erişilebilirlik;
- bilinmeyen değişken;
- node ayarları;
- input / output şemaları;
- limitler.

Bağımlılık kontrolleri (test ve yayında engelleyici, kayıtta uyarı):
- LLM profili var, etkin ve izinli listede;
- `max_tokens` model kapasitesi içinde;
- Claude 5 ailesinde temperature desteklenmez;
- structured outputs desteği;
- connector var;
- tool agent allowlist'inde, connector onayında, platform policy'sinde ve zorunlu Tool allowlist middleware'inde izinli;
- yürütme adapter'ı var (bugün yalnız Dynatrace `execute-dql`).

## Çalışma

`builder/engine.py` tanımı gerçek bir LangGraph `StateGraph`'ına derler; soru-cevapta AsyncPostgresSaver checkpointer'ı kullanılır. Özel agent'lar süreç genelindeki LLM ayarını değiştirmez. Her run, node'un çözülmüş profilinden kendi istemcisini (TracedChatAnthropic) kurar; bu sayede farklı modeldeki iki agent eşzamanlı çalışabilir.

Her run'a aynı platform kontrolleri uygulanır:
- platform agent kapısı (`check_allowed`);
- zorunlu middleware'ler: redaction, call limit, tool allowlist, parametre doğrulama (agent yalnız zorunlu olmayanları kapatabilir);
- policy (`tool.call`);
- netguard;
- RunBudget (LLM çağrı ve token sınırı);
- toplam timeout ve maksimum adım.

Run'lar şu ekranlara düşer: Agent Trace (`kind=custom_agent`; agent, sürüm ve mod alanlarıyla), Context Windows, Middleware execution kayıtları ve LLM kullanımı.

## Yönlendirme

- **Soru-Cevap:** Gönder düğmesinin yanındaki seçici yalnız erişilebilir, soru-cevap modlu ve yayımlanmış agent'ları listeler. Seçim boşsa platform varsayılanı (Agent Builder › Soru-Cevap varsayılanı, admin), o da yoksa gömülü agent kullanılır. Konuşma kendi agent'ına bağlıdır: agent değiştirmek yeni konuşma başlatır ve arayüz bunu bildirir. Farklı bir agent belirten istek 409 döner.
- **Schedule:** Settings › Scheduling › Agent schedule'ları.
  - Health-check'in agent seçimi KV `agent_builder.schedule_agent.health-check` anahtarında saklanır. Mevcut kurulum gömülü agent ile çalışmaya devam eder.
  - Yeni schedule'lar `agent_run` işi olarak oluşur (`agent:<ad>`).
  - Agent yayımlanmamışsa, arşivlenmişse, modu yoksa ya da schedule sahibinin erişimi kalkmışsa run `rejected` olarak kaydedilir ve iş nedeniyle birlikte başarısız olur. Başka bir agent'a geçilmez.
- **Arşiv:** Agent varsayılan soru-cevap agent'ı ya da bir schedule'da kullanılıyorsa bu bağımlılıklar listelenir; önce onların değiştirilmesi gerekir.

## İçe / dışa aktarma

JSON paketinde `schema_version: 1` ve `agent_version` bulunur. İçerik:
- ad, açıklama ve modlar;
- node'lar ve bağlantılar (yerel node kimlikleri korunur);
- yerleşim, prompt'lar, model ve runtime ayarları, I/O şemaları.

Bağlantılar eşleştirilebilir referansla (`llm:N`, `mcp:N`) yazılır; MCP tool'u için ad ve tanım özeti (hash) eklenir. Pakete dahil edilmeyenler: secret, konuşma, checkpoint, run, RAG içeriği, schedule.

Serbest metin alanlarında secret ya da PII kalıbı bulunursa dışa aktarma engellenir ve ilgili alanlar listelenir.

İçe aktarma:
- backend boyutu (512 KB), şemayı, paket sürümünü ve workflow bütünlüğünü doğrular; hiçbir şey çalıştırmaz;
- önizlemede ad, sürüm, modlar, node sayısı ve eşleştirilecek bağımlılıklar görünür;
- eşleştirme uyumluluğu kontrol edilir: model ayarları, tool adı ve hash;
- hedef yeni agent (ad çakışırsa yeniden adlandırılır) ya da mevcut özel agent'tır (yeni taslak revizyonu);
- eksik eşleştirme taslak olarak kaydedilir ve node'larda sorun olarak gösterilir;
- hata olursa kısmi kayıt kalmaz;
- gömülü agent import hedefi olamaz.

## Yetki ve audit

| Yetki | Roller | Kapsam |
|---|---|---|
| `agent:run` | SRE, admin | agent seçici, çalıştırma |
| `agent:build` | SRE, admin | oluşturma, taslak, test, kopya, import, export |
| `agent:publish` | admin | yayımlama, sürüm aktifleştirme, arşiv, Soru-Cevap varsayılanı |
| `settings:write` | admin | schedule agent seçimi |
| `observability:read` | SRE, admin | run kayıtları (soru-cevap run'ını yalnız sahibi ve admin görür) |

Audit kayıtları: `agent.create`, `agent.publish`, `agent.version.activate`, `agent.archive`, `agent.default.change`, `agent.import`, `agent.export`, `schedule.*`.

## API (özet)

`GET/POST /api/agents`, `GET /api/agents/catalog`, `GET /api/agents/{id}`, `PUT /api/agents/{id}/draft`, `POST /api/agents/{id}/validate|copy|publish|archive|test`, `GET /api/agents/{id}/versions/{n}`, `POST /api/agents/{id}/versions/{n}/activate`, `GET /api/agents/{id}/export?version=`, `POST /api/agents/import/preview`, `POST /api/agents/import`, `GET/PUT /api/agents/default`, `GET/POST/PUT/DELETE /api/agents/schedules`, `GET /api/agents/runs`, `POST /api/agents/runs/{run_id}/retry`. `POST /api/agent/chat` `agent_id` alanını kabul eder. `GET /api/agent-traces` `agent_id`, `agent_version` ve `agent_mode` filtrelerini kabul eder.

Migration: `0018_agent_builder` (agents, agent_versions, agent_runs, conversations.agent_id, agent_trace_runs agent kolonları, sistem agent'ı tohumu).

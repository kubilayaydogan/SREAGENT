# ADR-007 SRE Agent mimarisi (Aşama 2, 2026-11-21)

## Bağlam
SRE Agent canlı telemetriyi yalnız Dynatrace üzerinden okumalı. DB'ye, Docker'a ya da uygulamalara bağlanmamalı. Agent:
* risk değerlendirmeli;
* kaynak ve etkilenen uygulamayı ayırmalı;
* sürekli izlemeli (watch).

LLM çıktısı denetlenebilir olmalı. Kaos/deney etiketleri nedensellik kanıtı olarak kullanılmamalı.

## Kararlar

| # | Karar | Gerekçe | Reddedilen |
|---|---|---|---|
| 1 | Veri kaynağı yalnız **Dynatrace Remote MCP `execute-dql`**. Sabit bir DQL kataloğu kullanılır (`signals.py`, 17 sinyal + 2 ek kanıt sorgusu) | Tüm sorgu biçimleri tenant üzerinde doğrulandı (§J). Model sorgu uyduramaz. MCP kesilince başka kaynağa geçilmez, kısmi rapor yazılır | Claude'a `execute-dql`'i serbest araç olarak vermek (prompt injection ve maliyet riski; sonuç tekrarlanamaz); DB'den doğrudan okuma (talimat dışı) |
| 2 | **Puan ve atıf deterministik** (Python kuralları). Claude yalnız soruyu yorumlar (structured output) ve bulguları Türkçe açıklar | Puan test edilebilir ve tekrarlanabilir olur. Claude yokken agent tam çalışır. Telemetri metni yalnız `<analysis_data>` içinde veri olarak gider | LLM'in puan vermesi; ReAct agent |
| 3 | LangGraph `StateGraph` + `SqliteSaver` (`/data/checkpoints.sqlite`), çalıştırma başına `thread_id = run_id` | Talimat. Yarıda kalan çalıştırma izlenebilir. Ek kanıt turu koşullu kenarla yapılır ve `max_iterations` ile sınırlıdır | Bellek içi checkpointer |
| 4 | Üç ayrı boyut: risk şiddeti (0–100 + bant), veri kapsamı (OK sinyal oranı + puan kapsamı), nedensellik güveni (high/medium/low) | Talimat. "Veri yok" yüksek güvenli "sorun yok" gibi okunmamalı | Tek bileşik puan |
| 5 | Gerçek sıfır ile veri yokluğunun ayrımı collector kanıtına dayanır: `waiters_total` yalnız başarılı ölçümde (0 dahil) yazılır | Boş log sorgusu tek başına "kilit yok" demek değildir | Boşu sıfır saymak |
| 6 | Gecikme kaynağı: span p95 (her iki pencerede ≥30 örnek varsa), yoksa Micrometer ortalaması. Her ikisinde de `/actuator` ve `/lab` uçları dışlanır | Tenant'ta span hacmi düzensiz. Micrometer sürekli akar. `/lab` kaos uçlarıdır, kullanıcı trafiği değildir | Yalnız span; `dt.service.request.*` (controller bazında bölünüyor) |
| 7 | Kilit kaynağı **kök engelleyici**dir: kendisi Lock beklemeyen blocker. Zincirdeki ara halkalar ayrı listelenir | E2'de checkin, başka checkin'leri de bekletiyordu; ham "en çok engelleyen" yanlış kaynak verirdi | Blocker sayısına göre sıralama |
| 8 | Watch deterministik çalışır: 1 dk tamamlanmış pencere, ingest gecikmesi 120 sn, 3 yeni pencere onayı, hysteresis, 10 dk cooldown, dedup. Son değerlendirilen pencere SQLite'ta tutulur. Claude yalnız olay açılırken çağrılır | Talimat; maliyet; yeniden başlatmada aynı pencere tekrar sayılmaz | Her tick'te LLM |
| 9 | Agent ayrı Docker ağındadır (`airlab-agent`); airlab ağına ve DB'ye erişimi yoktur. Gizli bilgiler `agent.env` dosyasındadır (600, gitignored) | En az yetki | Servis ağını paylaşmak |
| 10 | LangSmith yalnız redakte eden tracer ile kullanılır (`hide_inputs/hide_outputs`). Ortamdaki otomatik izleme bayrakları kapatılır | Token/anahtar sızıntısı engellenir. LangSmith yoksa agent çalışmaya devam eder | Ortam değişkeniyle otomatik izleme |
| 11 | Sürümler: langchain 1.5.2, langchain-core 1.8.4, langchain-anthropic 1.7.12, langgraph 1.2.24, langgraph-checkpoint-sqlite 3.1.3, langsmith 0.16.0, langchain-mcp-adapters 0.3.2, **mcp 1.30.0**, Python 3.13 | 2026-11-21 PyPI'dan doğrulandı. Adaptör `mcp<2` istediği için mcp 2.x seçilemedi | mcp 2.x |

## Sonuçlar
* Eşikler 2026-11-21 deneylerine göre kalibre edildi. Başka donanımda `AGENT_CONFIG` ile yeniden ayarlanmalı.
* `postgres.*` extension metrikleri yok (ADR/PROJECT_STATE). DB CPU nedeni (sorgu ya da bakım işi) ayrıştırılamaz; raporda alternatif açıklama olarak yazılır.
* APP_VM CPU mekanizması container CPU'larını karşılaştırır. Agent'ın kendi tüketimi `self_is_source` olarak ayrıca işaretlenir.

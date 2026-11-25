# airlab SRE Agent (Aşama 2)

airlab laboratuvarı (4 Java servisi + PostgreSQL 17) için **salt-okur** SRE asistanı. Tek canlı veri kaynağı
**Dynatrace Remote MCP** (`execute-dql`). DB'ye, Docker'a veya uygulamalara bağlanmaz. MCP kesilirse başka kaynağa
geçmez; kısmi rapor yazar.

| Katman | Seçim |
|---|---|
| Akış | LangGraph 1.2 `StateGraph` + `SqliteSaver` checkpoint (`/data/checkpoints.sqlite`) |
| MCP | langchain-mcp-adapters 0.3.2 / mcp 1.30 (streamable HTTP, Bearer platform token) |
| LLM | Claude `claude-opus-5-5` (langchain-anthropic). Yalnız iki iş yapar: soruyu yorumlamak ve bulguları Türkçe açıklamak |
| İzleme | LangSmith (girdi/çıktı redakte edilir; LangSmith yoksa agent çalışmaya devam eder) |
| Puan / atıf | Deterministik Python. Claude puanı değiştiremez, aracı yoktur |

## Akış

```
plan ─► collect (15 katalog DQL, tek MCP oturumu) ─► analyze (özellik + veri kalitesi + puan + mekanizma)
     ─► [extra_evidence: row_lock → edges_detail, db_cpu/bağlantı → top_statements; ≤ max_iterations]
     ─► explain (Claude; erişilemezse deterministik) ─► finalize (JSON + Markdown rapor)
```

* **Veri kalitesi:** her sinyal `OK | EMPTY | PERMISSION | ERROR | STALE | UNAVAILABLE | SKIPPED_BUDGET`.
  Boş sonuç yalnızca kaynağın o pencerede ölçüm yazdığı kanıtlanırsa **gerçek sıfır** sayılır (`true_zero`).
  Örnek: collector başarılıysa kilit kenarı yokluğu gerçekten 0'dır. Aksi halde değer `null` (bilinmiyor) olur.
* **Risk puanı (0–100):** gecikme, 5xx, doygunluk, kilit ve kaynak bileşenlerinin ağırlıklı ortalaması
  (`defaults.yaml → score`). Ölçülemeyen bileşen paydaya girmez. Ölçülen ağırlık `min_score_coverage` altında kalırsa
  puan `null` olur. Genel puan, uygulama puanlarının en yükseğidir.
* **Üç ayrı boyut:** risk şiddeti, veri kapsamı ve nedensellik güveni (`high/medium/low`) raporda ayrı alanlardır.
* **Mekanizmalar:** `row_lock`, `pool_saturation` (tek havuz; kilidin sonucuysa `secondary_to`),
  `global_connection_pressure` (mutlak eşik veya göreli artış), `db_cpu`, `app_vm_cpu` (agent'ın kendi CPU'su ayrı işaretlenir: `self_is_source`), `app_container_cpu`, `network_path_latency`,
  `independent_load`. Kök engelleyici, kendisi Lock beklemeyen blocker'dır; zincirdeki ara halkalar kaynak sayılmaz.
* **Kaos etiketleri** (`experiment_id`, `lab_hold`) telemetri girişte silinir. Kanıt olarak kullanılmaz, rapora ve
  Claude'a gitmez.

## CLI

```bash
sre-agent doctor                       # yapılandırma, MCP araç keşfi, zorunlu sinyaller, Claude, LangSmith
sre-agent analyze --last 15            # son 15 dk (ingest gecikmesi 120 sn düşülür)
sre-agent analyze --from 2026-11-21T14:52:00Z --to 2026-11-21T14:56:00Z \
                  --baseline-from 2026-11-21T14:47:00Z --baseline-to 2026-11-21T14:51:00Z
sre-agent ask "son 30 dakikada checkin neden yavaş?"
sre-agent chat [--thread chat-xxxx]    # oturum geçmişi /data/state.sqlite'ta
sre-agent watch [--once]               # sürekli izleme (normal döngüde Claude yok)
sre-agent watch-status                 # açık olay, koşul sayaçları, son olaylar
sre-agent report list | report show <id> [--json]
```

`--no-llm` her komutta Claude'u kapatır (deterministik açıklama).

## Watch

* Tamamlanmış 1 dk pencereler. Pencere sonu `now − ingest_lag_seconds` olur.
* Her pencere bir kez değerlendirilir; yeniden başlatmada aynı pencere tekrar sayılmaz.
* Olay açmak için aynı koşulun art arda 3 **yeni** pencerede `open` eşiğini aşması gerekir.
* Kapanış (recovery) için 3 pencere `clear` eşiğinin altında kalmalıdır (hysteresis). Kapanıştan sonra 10 dk cooldown uygulanır.
* Açık olay varken yeni koşullar o olaya eklenir (dedup).
* Veri boşluğu sıfır sayılmaz. Uzun boşluk `telemetry_gap` olayı üretir.
* MCP kesintisinde pencereler ilerletilmez; MCP dönünce kaçırılan pencereler (en fazla 10) değerlendirilir.
* Claude yalnız olay **açılırken** çağrılır (`watch.llm_on_incident`). Recovery raporu deterministiktir.

## Yapılandırma

| Ortam değişkeni | Açıklama |
|---|---|
| `DT_ENV` / `DT_MCP_URL` | Tenant kimliği (URL ondan türetilir) ya da tam MCP URL'si |
| `DT_PLATFORM_TOKEN` | MCP platform token (gizli) |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | Claude (varsayılan `claude-opus-5-5`, effort `medium`); anahtar yoksa deterministik mod |
| `CLAUDE_RCA_MODEL` | RCA açıklamasını yazan model (varsayılan `claude-opus-5-5`, effort `high`, refusal'da sunucu tarafı fallback). Soru yorumlama ve sohbet `CLAUDE_MODEL` ile (o da varsayılan Opus 5.5) |
| `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT` | İzleme (redakte) |
| `AGENT_DATA_DIR` | Durum, checkpoint, rapor dizini (container'da `/data`) |
| `AGENT_CONFIG` | `defaults.yaml` üzerine birleştirilecek YAML (eşikler, ağırlıklar, bütçeler) |

Bütçeler (`budgets`): çalıştırma başına sorgu sayısı, ek kanıt turu, toplam süre, sorgu zaman aşımı/yeniden deneme,
LLM token/zaman aşımı/yeniden deneme.

## Test ve değerlendirme

```bash
pytest -q                      # birim + senaryo + graf + watch (sahte MCP; ağ gerekmez)
python eval/run_eval.py        # 10 sentetik senaryo (etiketler eval/labels.yaml; agent girdisinden ayrı)
python eval/run_eval.py --real # gerçek VM deney pencereleri, MCP üzerinden (eval/real_windows.yaml + real_labels.yaml)
```

Sentetik raporlar `fixture: true` ile işaretlenir ve Markdown'da "FIXTURE / SENTETİK VERİ" uyarısı taşır.

## Dağıtım (APP_VM)

```bash
scripts/gen-agent-env.sh                               # deploy/out/secrets.env → deploy/out/agent.env (600)
scp deploy/out/agent.env airlab-app:/opt/airlab/app-vm/agent.env
cd /opt/airlab/app-vm
docker compose --profile agent build sre-agent
docker compose --profile agent-cli run --rm sre-agent-cli doctor
docker compose --profile agent up -d sre-agent         # watch
docker compose --profile agent-cli run --rm sre-agent-cli ask "son 15 dakikada sorun var mı?"
```

Ayrıntı: `docs/sre-agent-runbook.md`, `docs/decisions/ADR-007-sre-agent.md`.

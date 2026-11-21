# SRE Agent runbook (Aşama 2)

Kod: `sre-agent/` (ayrıntı `sre-agent/README.md`). Karar kaydı: `docs/decisions/ADR-007-sre-agent.md`.

## 1. Kurulum / güncelleme (APP_VM)

```bash
# Windows (repo kökü)
scripts/gen-agent-env.sh                                   # deploy/out/secrets.env → deploy/out/agent.env
scp deploy/out/agent.env airlab-app:/opt/airlab/app-vm/agent.env
tar --exclude=.venv --exclude=data --exclude=__pycache__ --exclude=eval/results -czf /tmp/sa.tgz sre-agent app-vm/docker-compose.yml
scp /tmp/sa.tgz airlab-app:/tmp/ && ssh airlab-app 'cd /opt/airlab && tar xzf /tmp/sa.tgz'

# APP_VM
cd /opt/airlab/app-vm && chmod 600 agent.env
docker compose --profile agent build sre-agent             # yavaş bağlantıda ~10–15 dk (pip)
docker compose --profile agent-cli run --rm sre-agent-cli doctor
docker compose --profile agent up -d sre-agent
```

`doctor` çıktısında beklenenler:
* MCP araç listesinde `execute-dql` var;
* 8 zorunlu sinyal `OK` ya da `EMPTY`;
* Claude `OK`;
* LangSmith `OK`.

## 2. Günlük kullanım

```bash
alias sa='docker compose -f /opt/airlab/app-vm/docker-compose.yml --profile agent-cli run --rm sre-agent-cli'
sa ask "son 30 dakikada checkin neden yavaş?"
sa analyze --last 15
sa chat                                   # çıkış /q; devam: sa chat --thread <id>
sa watch-status                           # açık olay + son olaylar
sa report list ; sa report show <id>
docker logs -f airlab-sre-agent           # watch tick'leri ve olaylar
```

Raporlar `agent-data` volume'ünde (`/data/reports/<id>.json|.md`). Dışarı almak için:
`docker cp airlab-sre-agent:/data/reports ./reports`.

## 3. Arıza durumları

| Belirti | Anlamı | Eylem |
|---|---|---|
| Rapor `status: partial`, kanıtlar `UNAVAILABLE` | MCP'ye ulaşılamadı. Başka kaynağa geçilmedi | `doctor`. Platform token süresi/kapsamı, tenant URL'si (trial bitti mi?), APP_VM dış ağı |
| Bir sinyal `PERMISSION` | Token kapsamı eksik | Platform token'a `storage:*:read` kapsamlarını ekleyin |
| `STALE` / `EMPTY` (http, hosts, collector) | Telemetri hattı sorunlu | OneAgent, `pg-lock-collector` (`docker logs airlab-pg-lock-collector`), ingest token |
| `explanation_source: deterministik (Claude erişilemedi)` | Anthropic API'ye ulaşılamadı | Rapor yine geçerli; anahtar ve kota kontrolü |
| Watch olayında `telemetry_gap` | 5 dk boyunca o koşul için veri yok | Sıfır değil, **bilinmiyor** demektir; hattı kontrol edin |
| Watch `mcp_unavailable` | MCP kesintisi | Pencereler ilerletilmez. MCP dönünce kaçırılan pencereler (≤10) değerlendirilir |
| `app_vm_cpu` mekanizmasında `self_is_source: true` | Agent'ın kendi CPU tüketimi artışın kaynağı | `watch.poll_seconds`'ı büyütün; `cpus: 0.5` sınırı compose'da |

## 4. Durdurma / temizlik

```bash
docker compose --profile agent stop sre-agent           # SIGTERM; durum SQLite'ta kalır
docker compose --profile agent rm -f sre-agent
docker volume rm app-vm_agent-data                       # DİKKAT: raporlar, checkpoint ve watch durumu silinir
```

## 5. Değerlendirme

```bash
cd /opt/airlab/app-vm
docker compose --profile agent-cli run --rm --entrypoint python sre-agent-cli -m pytest -q -p no:cacheprovider /app/tests
docker compose --profile agent-cli run --rm --entrypoint python sre-agent-cli /app/eval/run_eval.py --real --data-dir /tmp/eval
```

Etiketler (`eval/labels.yaml`, `eval/real_labels.yaml`) yalnız karşılaştırmada okunur; agent girdisine girmez.

## 6. Yeni tenant

`secrets.env` içinde `DT_ENV` ve `T_MCP` değerlerini değiştirin, ardından `gen-agent-env.sh` ve `doctor` çalıştırın.
Metrik anahtarları (`airlab.*`) ve log alanları Aşama 1 telemetri sözleşmesinden gelir; yeni tenant'ta da aynı
ingest ayarlarıyla oluşur.

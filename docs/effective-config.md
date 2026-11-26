# Etkili yapılandırma: bir çalıştırmada hangi agent, model ve connector kullanılır?

Bu doküman, bir çalıştırmanın (run) hangi değerlerle çalıştığını ve bu değerlerin nereden geldiğini anlatır. Değerler
koşu başında bir kez çözülür ve dondurulur; koşu sürerken yapılan ayar değişikliği o koşuyu etkilemez, bir sonraki
koşudan itibaren geçerlidir. Backend ve worker ayrı süreçlerdir; ikisi de her koşu başında ayarları veritabanından okur,
yeniden başlatma gerekmez ("restart" işaretli alanlar hariç: `max_concurrent_runs`, LangSmith project, watch ayarları).

## Agent

| Giriş noktası | Seçim | Kaynak |
|---|---|---|
| Soru-Cevap (yeni konuşma) | Kullanıcının seçtiği agent; seçim yoksa platform varsayılanı | İstek `agent_id` > KV `agent_builder.default_chat_agent` > gömülü SRE Agent |
| Soru-Cevap (mevcut konuşma) | Konuşmanın başladığı agent | `conversations.agent_id` (farklı agent → 409, yeni konuşma gerekir) |
| Health check (scheduler) | Schedule'da seçili agent | KV `agent_builder.schedule_agent.health-check` > gömülü SRE Agent |
| Agent schedule'ı | Schedule'ın agent'ı | `scheduled_jobs.params.agent_id` |
| Manuel investigation, watch | Gömülü SRE Agent | Sabit |

Özel agent her koşu başında **o an aktif yayımlanmış sürümüyle** çalışır (retry aynı sürümü korur). Yayımlanmamış,
arşivlenmiş ya da modu desteklemeyen agent çalıştırılmaz; başka agent'a sessizce geçilmez, neden kullanıcıya ve run
kaydına yazılır.

## Model

### Gömülü SRE Agent

Görev başına (`chat`: soru yorumlama ve sohbet yanıtı, `rca`: analiz açıklaması) öncelik:

1. **Üst bardaki model seçimi** (`llm.model_override`): iki görevin de modelini belirler.
2. **Görev LLM profili** (Settings › LLM Providers: `chat_profile`, `rca_profile`).
3. **Ortam değişkeni** (`CLAUDE_MODEL`, `CLAUDE_RCA_MODEL`).

Kurallar:

* Üst bar seçimi de **model allowlist**'ine (`SRE_LLM_MODEL_ALLOWLIST`) tabidir. İzinli olmayan kayıtlı seçim yok
  sayılır (profil / ortam geçerli olur), karar koşu notuna, audit'e (`llm.model_denied`) ve
  `config_snapshot.llm.model_override_ignored` alanına yazılır. İzinli olmayan model üst bardan ya da Settings'ten
  kaydedilemez (422).
* Profil kullanılamıyorsa (devre dışı, bütçe doldu, anahtar çözülemedi, model izinli değil) o görevde LLM kullanılmaz;
  üst bar seçimi görevi yeniden açmaz.
* Üst bar seçimi kaldırılabilir (`PUT /api/system/llm-model {"model": null}`); seçici bu durumu "Otomatik: <model>
  (<kaynak>)" olarak gösterir.
* Manuel ve zamanlanmış koşu aynı önceliği uygular.

Analiz açıklamasında LLM kullanılıp kullanılmayacağı ayrıca şunlara bağlıdır:

| Çalışma türü | Koşul |
|---|---|
| Manuel investigation | "Claude ile açıklama üret" seçimi (çalıştırmaya özel) |
| Health check | Settings › Agent › `scheduled_llm` |
| Watch incident / recovery | Settings › Agent › `incident_llm` |
| Hepsi | Settings › Agent › `llm_enabled` ve API anahtarının varlığı |

### Özel agent (Agent Builder)

Node ayarı > agent varsayılanı > LLM profilinin modeli. Üst bardaki seçim özel agent'ları **etkilemez**; her koşu kendi
LLM istemcisini kurar. Allowlist, profil bütçesi ve egress kontrolü aynen uygulanır.

## MCP connector

Gömülü SRE Agent `analysis_source` işaretli connector'ı kullanır. Query timeout ve retry, agent ayarı ile connector
sınırının **küçüğüdür**. Connector yoksa, policy izin vermiyorsa, secret çözülemiyorsa ya da adres ağ kuralına takılırsa
MCP kullanılamaz: çalıştırma kısmi rapor üretir, örnek veri üretilmez. Özel agent'ta her MCP node'u kendi connector
seçimini kullanır.

## Kayıt: neyin kullanıldığı nereden görülür?

| Kayıt | Alan | İçerik |
|---|---|---|
| `investigations.config_snapshot.llm.effective` | `chat`, `rca` | `{model, source, profile?, reason?}`; `source`: `override` \| `profile` \| `env` \| `none` |
| `investigations.config_snapshot.llm.run` | | Bu koşuda açıklama için LLM kullanıldı mı (`used`), kullanılmadıysa `reason` (`run_choice`, `scheduled_llm_off`, `incident_llm_off`, `llm_disabled`, `profile_*`, `llm_not_configured`) |
| `investigations.config_snapshot.connector` | | Connector adı, revizyonu, etkili timeout / retry, kullanılabilirlik |
| `agent_runs.models[]` | | Koşuda geçerli model + `source` (LLM kullanılmayan koşuda boş) |
| Sohbet mesajı `metadata` | `run_id`, `llm` | Turun kimliği (Agent Trace anahtarı) ve tur başında dondurulan model seçimi |

`config_snapshot.hash` yalnız girdilerden (ayar revizyonları, etkili değerler, connector / profil revizyonları) hesaplanır;
`effective` ve `run` alanları hash'e girmez, bu yüzden eski ve yeni koşuların hash'i karşılaştırılabilir kalır.

Zamanlanmış health check'in run kaydı (`agent_runs.run_id`) investigation'ın `run_id` değeriyle aynıdır; Agent Trace,
MCP çağrıları ve Context Windows bağlantıları tek kimlik üzerinden çalışır.

Arayüzde: investigation detayında "Bu çalıştırmada kullanılanlar" kartı, sohbet yanıtının altındaki model satırı,
Agent Builder › Çalıştırmalar tablosundaki model + kaynak sütunu ve Soru-Cevap kutusunun altındaki model notu.

## Settings kayıt davranışı

* Bir alan yalnız ortam / varsayılan değerinden **farklıysa** saklanır. Ortam değeriyle aynı değeri kaydetmek o alanı
  "ortamı izler" durumunda bırakır: ortam değeri sonradan değişirse alan onu izler.
* `false` ve `0` geçerli değerlerdir ve saklanır (ör. `scheduled_llm=false`).
* Deployment lock'u olan alan arayüzden değiştirilemez; kaynak rozeti "locked" görünür.
* Geçersiz kayıtlı bölüm (ör. elle düzenlenmiş) ortam / varsayılan değerlerine döner ve log'a yazılır.

## Eksik veri, hata ve yarıda kalan çalıştırma

* **Yanıt vermeyen query 0 sayılmaz.** `ERROR`, `TIMEOUT`, `PERMISSION`, `UNAVAILABLE`, `SKIPPED_BUDGET` durumundaki
  signal eksik veridir: ilgili skor bileşeni hesaplanmaz, coverage düşer, rapor `partial` olur. `EMPTY` yanıt alınmış
  sonuçtur; Lock signal'lerinde yalnız collector o pencerede yazdıysa ve query'nin kendisi yanıt verdiyse "gerçek 0" olur.
* **Tek signal başarısızsa** kalan veriyle bulunan mechanism ve ek evidence korunur; eksik signal'ler raporda,
  sohbet yanıtının başındaki uyarıda ve investigation detayında adıyla yazılır.
* **LLM erişilemezse** açıklama deterministik üretilir; kaynak "deterministik (Claude erişilemedi)" olarak kaydedilir.
  Yanıt akarken kesilirse yarım metin ekrandan ve kayıttan kaldırılır (`answer_replaced`).
* **Yarıda kalan çalıştırma** (süreç yeniden başladı): worker dakikada bir, kendi süre sınırını + 5 dk aşmış
  `RUNNING` / `PENDING` investigation, iş ve agent çalıştırma kayıtlarını `FAILED` olarak kapatır
  (`services/reconcile.py`); sonuç üretilmez.
* **Scheduler:** bir turda kiralanan her iş başlamadan önce kirasını yeniler; kira başka worker'a geçtiyse iş
  çalıştırılmaz (aynı iş iki kez çalışmaz).

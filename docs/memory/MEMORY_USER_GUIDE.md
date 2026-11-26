# Agent Memory — User Guide

Platform › **Agent Memory** (rol: SRE ve admin). Üst barda agent, environment (lab), zaman aralığı (1h / 24h / 7d / 30d)
ve yenile düğmesi bulunur. Seçimler URL'de tutulur; link paylaşılabilir.

## Sekmeler

| Sekme | Ne için |
|---|---|
| **Overview** | Toplam kayıt, thread, checkpoint; read/write sayıları; retrieval success ve empty rate; p50/p95 latency; context token; storage; backend health. "Metric tanımları" bölümü her sayının nasıl hesaplandığını yazar. Ölçüm yoksa "—" görünür. |
| **Working Memory** | Çalışan veya bitmiş bir agent çalışmasının state'i: current node, iteration, pending/completed task'lar, evidence, risk score, root cause adayları. Çalışma sürerken 2 sn'de bir yenilenir. Ayrıntı için Agent Trace linki. |
| **Short-Term Memory** | Thread listesi (sohbet ve analiz). Sohbet thread'inde mesajlar, hangi mesajın state penceresinde olduğu (son 20), digest özetleri, tool çağrıları, Agent Trace ve LangSmith linkleri. Başkasının sohbeti görünmez (admin hariç). |
| **Long-Term Memory** | Semantic, episodic ve procedural kayıtlar tek tabloda; kaynak (backing), doğrulama, onay ve durum filtreleri. Kayda tıklayınca detay, versiyon geçmişi ve eylemler açılır. |
| **Checkpoints** | Thread seçip checkpoint timeline'ını gezin: state values, parent → bu checkpoint diff'i, iki checkpoint karşılaştırma, metadata, pending writes, next nodes. Salt okunur. |
| **Memory Retrieval** | Playground (salt okunur deneme) ve agent'ın gerçek retrieval geçmişi: pipeline aşamaları, seçilen ve dışlanan kayıtlar (neden), context injection. |
| **Memory Activity** | Tüm memory olayları; tür / durum / run filtresi; **Live** ile canlı akış. Olaya tıklayınca maskeli payload. |
| **Memory Governance** | Uygulanan kontroller, izin matrisi, policy ihlalleri ve memory audit kayıtları. |
| **Settings** | `agent_memory` ayarları (admin). Değişiklik versiyonlanır, bir sonraki çalışmadan itibaren geçerlidir. |

## Sık işlemler

**Yeni olgu eklemek** — Long-Term › *New memory* → Semantic, başlık, içerik, kategori (ör. `topology`). Kaynak
`manual`/`import` güvenilir sayılır. `mcp`, `log`, `agent_extraction`, `external` güvenilmezdir: kayıt `unverified`
olur ve doğrulanana kadar agent'a verilmez. Secret veya talimat benzeri içerik reddedilir; red nedeni formda görünür.

**Doğrulamak / reddetmek** — Kayıt detayında *Verify* veya *Reject*. Reddedilen kayıt retrieval dışıdır.

**Runbook (procedural) önermek** — *New memory* → Procedural. Öneri `pending` olur, agent görmez. Başka bir admin
*Approve* ile canlıya alır (öneren onaylayamaz). Mevcut runbook'ta *Değişiklik öner* canlı metni değiştirmez; onay
bekleyen revizyon oluşturur.

**Arşivlemek / silmek** — *Archive* geri alınabilir (*Restore*). Kalıcı silme yalnız admin içindir, yalnız
`sre.memory_records` kayıtlarında vardır ve onay için kayıt kimliğini aynen yazmanız gerekir. Versiyon geçmişi kalır.

**Agent neden bu geçmişi kullandı?** — Memory Retrieval › Retrieval history'de ilgili run'ı açın: *Validation*
aşaması hangi kaydın neden dışlandığını, *Context injection* hangi bölümün LLM'e kaç token ile gittiğini gösterir.

## Bilinmesi gerekenler

* Semantic ve Hybrid (vector) arama yoktur; seçenekler devre dışı görünür.
* Token sayıları tahmindir (karakter / 4).
* `analysis_memory_enabled` açılırsa analiz raporuna "Historical context" bölümü eklenir; bu bölüm evidence değildir
  ve risk score'u değiştirmez.
* Checkpoint'ten replay / fork / resume yapılamaz (dış sistemlerde yan etki riski).

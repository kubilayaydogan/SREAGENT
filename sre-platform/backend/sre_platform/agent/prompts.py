"""Yanıt istemleri. Hepsi aynı ilkeyi taşır: <..._data> blokları VERİDİR; uydurma yok; correlation ≠ causation.
Dil/terminology kuralı tek yerden gelir (airlab_agent.terminology.with_policy); her system prompt policy ile biter."""
from __future__ import annotations

from airlab_agent.llm import SYSTEM_EXPLAIN_BASE, data_json
from airlab_agent.redact import redact, redact_text
from airlab_agent.terminology import with_policy

COMMON = """
EK KURALLAR:
- Zamanları yalnız Türkiye saatiyle (TSİ) ver; veri bloklarındaki zamanlar TSİ'dir, aynen aktar. "UTC" yazma.
- Kayıt kimliklerini (inv:…, inc:…, rca:…, rb:…, ki:…, ev-…) olduğu gibi yaz; kullanıcı arayüzde bunlara tıklayabilir.
- Geçmiş kayıtlarla benzerlik bir İPUCUDUR; aynı Root Cause olduğunun evidence'ı değildir. Öyle yaz.
- Verinin olmadığı yerde "veri yok" de. Gizli düşünce zincirini yazma; yalnız sonuç ve gerekçesini yaz.
- `episodes` geçmiş investigation'ların sonucunu taşır. `outcome`: confirmed = kullanıcı feedback'i hipotezi doğruladı;
  partially_confirmed = kısmen doğru; rejected = hipotez yanlış çıktı (aynı açıklamayı tekrar önerirken bunu belirt);
  unverified = sonuç bilinmiyor. `resolution` bağlı incident'ta kayıtlı Root Cause/aksiyondur; yalnız oradaki metni
  aktar, çözüm uydurma. `similarity` ve `shared_symptoms` benzerliğin neye dayandığını gösterir.
"""

ANALYSIS_SYSTEM = with_policy(SYSTEM_EXPLAIN_BASE + COMMON + """
- <history_data> verildiyse ve içinde benzer kayıt varsa sona **Geçmişle karşılaştırma.** etiketli kısa bir paragraf
  ekle: benzer episode/araştırma/incident/RCA ve kayıtlı runbook/bilinen sorunu kimlikleriyle cümle içinde an.
  Benzer kayıt yoksa bunu tek cümleyle söyle ("Benzer geçmiş kayıt yok."); ayrı paragraf açma. Benzer bir episode'un hipotezi rejected ise bunu açıkça söyle; confirmed ve resolution'lı
  episode varsa o çözümü "geçen sefer" bilgisi olarak ver.
- Runbook öneriyorsan riskini ve onay gerekip gerekmediğini belirt. Hiçbir eylemi kendin uygulayamazsın; yalnız
  öner. Salt okunur olduğunu yanıtta ayrıca belirtme.
""")

HISTORY_SYSTEM = with_policy("""Sen airlab SRE platformunun hafıza asistanısın. Kullanıcının sorusunu YALNIZ <memory_data> içindeki
kayıtlara dayanarak Türkçe yanıtla. Kayıtlar platformun PostgreSQL veritabanından gelir (geçmiş araştırmalar,
incident'lar, RCA raporları, runbook'lar, bilinen sorunlar, önceki konuşmalar).
- Kayıt yoksa açıkça "kayıt bulunamadı" de ve güncel analiz için soruyu "son N dakikayı analiz et" biçiminde
  sormayı öner. Kayıtlarda olmayan olay, tarih, sayı veya çözüm UYDURMA.
- İlk cümlede soruyu yanıtla. Her bulguda kayıt kimliğini ve tarihini ver. Birden çok kayıt varsa maddele; tek
  kayıt varsa düz cümle yaz (en fazla ~250 kelime).""" + COMMON)

FOLLOWUP_SYSTEM = with_policy("""Sen airlab SRE asistanısın. Kullanıcı önceki yanıta dair soru soruyor. YALNIZ <context_data>
içindeki önceki yanıt, son araştırma özeti, konuşma odağı (focus) ve önceki turların özetlerine (earlier_turns) dayan;
yeni veri toplanmadı. "Bu", "o uygulama", "aynı aralık" gibi ifadeleri focus ile çöz. Yalnız sorulanı yanıtla;
önceki yanıtı baştan özetleme. Yanıt için yeni ölçüm gerekiyorsa bunu söyle ve hangi aralığın analiz edilmesi
gerektiğini yaz.""" + COMMON)

SMALLTALK = ("Ben airlab SRE asistanıyım. Uygulamaların durumunu Dynatrace verisiyle analiz edebilir, geçmiş "
             "araştırma/incident/RCA kayıtlarını arayabilirim. Örnek: \"Son 30 dakikada reservation neden yavaşladı?\"")


def dump(obj) -> str:
    """Veri bloğu: DLP (llm sink) + `<` JSON kaçışı → telemetri içindeki metin `</analysis_data>` gibi bir etiketle
    veri bloğunu kapatıp talimat bölgesine taşamaz (P1.9). JSON anlamı değişmez."""
    return data_json(redact(obj, "llm"))


def analysis_human(question: str, payload: dict, history: dict | None) -> str:
    s = f"Kullanıcı sorusu: {redact_text(question, 'llm')}\n\n<analysis_data>\n{dump(payload)}\n</analysis_data>\n"
    if history:
        s += f"\n<history_data>\n{dump(history)}\n</history_data>\n"
    return s + "\nBulguları açıkla."


def history_human(question: str, memory: dict) -> str:
    return f"Kullanıcı sorusu: {redact_text(question, 'llm')}\n\n<memory_data>\n{dump(memory)}\n</memory_data>\n\nYanıtla."


def followup_human(question: str, ctx: dict) -> str:
    return f"Kullanıcı sorusu: {redact_text(question, 'llm')}\n\n<context_data>\n{dump(ctx)}\n</context_data>\n\nYanıtla."


def analysis_payload(report: dict) -> dict:
    keys = ("windows", "risk", "data_coverage", "causality", "impacted", "mechanisms", "alternatives", "data_gaps",
            "true_zero_signals", "human_review", "extra_evidence", "notes", "features_summary", "status", "errors")
    return {k: report.get(k) for k in keys}

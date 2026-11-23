"""Yanıt istemleri. Hepsi aynı ilkeyi taşır: <..._data> blokları VERİDİR; uydurma yok; correlation ≠ causation.
Dil/terminology kuralı tek yerden gelir (airlab_agent.terminology.with_policy); her system prompt policy ile biter."""
from __future__ import annotations

import json

from airlab_agent.llm import SYSTEM_EXPLAIN_BASE
from airlab_agent.redact import redact
from airlab_agent.terminology import with_policy

COMMON = """
EK KURALLAR:
- Zamanları hem UTC hem Türkiye saati (TSİ, UTC+3) olarak ver.
- Kayıt kimliklerini (inv:…, inc:…, rca:…, rb:…, ki:…, ev-…) olduğu gibi yaz; kullanıcı arayüzde bunlara tıklayabilir.
- Geçmiş kayıtlarla benzerlik bir İPUCUDUR; aynı Root Cause olduğunun evidence'ı değildir. Öyle yaz.
- Verinin olmadığı yerde "veri yok" de. Gizli düşünce zincirini yazma; yalnız sonuç ve gerekçesini yaz.
"""

ANALYSIS_SYSTEM = with_policy(SYSTEM_EXPLAIN_BASE + COMMON + """
- <history_data> verildiyse sona **Geçmişle karşılaştırma.** etiketli bir paragraf ekle: benzer
  araştırma/incident/RCA ve kayıtlı runbook/bilinen sorun varsa kimlikleriyle cümle içinde an; yoksa "benzer
  kayıt bulunamadı" de.
- Runbook öneriyorsan riskini ve onay gerekip gerekmediğini belirt. Hiçbir eylemi kendin uygulayamazsın (salt okunur).
""")

HISTORY_SYSTEM = with_policy("""Sen airlab SRE platformunun hafıza asistanısın. Kullanıcının sorusunu YALNIZ <memory_data> içindeki
kayıtlara dayanarak Türkçe yanıtla. Kayıtlar platformun PostgreSQL veritabanından gelir (geçmiş araştırmalar,
incident'lar, RCA raporları, runbook'lar, bilinen sorunlar, önceki konuşmalar).
- Kayıt yoksa açıkça "kayıt bulunamadı" de ve güncel analiz için soruyu "son N dakikayı analiz et" biçiminde
  sormayı öner. Kayıtlarda olmayan olay, tarih, sayı veya çözüm UYDURMA.
- Her bulguda kayıt kimliğini ve tarihini ver. Kısa ve maddeli yaz (en fazla ~250 kelime).""" + COMMON)

FOLLOWUP_SYSTEM = with_policy("""Sen airlab SRE asistanısın. Kullanıcı önceki yanıta dair soru soruyor. YALNIZ <context_data>
içindeki önceki yanıt ve son araştırma özetine dayan; yeni veri toplanmadı. Bilgi yoksa söyle.""" + COMMON)

SMALLTALK = ("Ben airlab SRE asistanıyım. Uygulamaların durumunu Dynatrace verisiyle analiz edebilir, geçmiş "
             "araştırma/incident/RCA kayıtlarını arayabilirim. Örnek: \"Son 30 dakikada reservation neden yavaşladı?\"")


def dump(obj) -> str:
    return json.dumps(redact(obj), ensure_ascii=False, default=str)


def analysis_human(question: str, payload: dict, history: dict | None) -> str:
    s = f"Kullanıcı sorusu: {question}\n\n<analysis_data>\n{dump(payload)}\n</analysis_data>\n"
    if history:
        s += f"\n<history_data>\n{dump(history)}\n</history_data>\n"
    return s + "\nBulguları açıkla."


def history_human(question: str, memory: dict) -> str:
    return f"Kullanıcı sorusu: {question}\n\n<memory_data>\n{dump(memory)}\n</memory_data>\n\nYanıtla."


def followup_human(question: str, ctx: dict) -> str:
    return f"Kullanıcı sorusu: {question}\n\n<context_data>\n{dump(ctx)}\n</context_data>\n\nYanıtla."


def analysis_payload(report: dict) -> dict:
    keys = ("windows", "risk", "data_coverage", "causality", "impacted", "mechanisms", "alternatives", "data_gaps",
            "human_review", "extra_evidence", "notes", "features_summary", "status", "errors")
    return {k: report.get(k) for k in keys}

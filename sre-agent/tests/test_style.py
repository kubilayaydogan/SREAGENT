"""Yazım üslubu: ortak kurallar kullanıcıya metin üreten her system prompt'a tek yerden, bir kez eklenir; veri
sözleşmelerine ve agent'a özel talimatlara dokunmaz."""
from __future__ import annotations

from airlab_agent import terminology as T
from airlab_agent.llm import SYSTEM_EXPLAIN, SYSTEM_EXPLAIN_BASE
from airlab_agent.style import STYLE_MARK, STYLE_PROMPT
from test_graph import FakeLlm, _report, _run, _with_failed, settings  # noqa: F401


def test_style_block_added_once_between_prompt_and_policy():
    custom = "Sen ödeme ekibinin asistanısın. Yanıtı İngilizce başlıklarla ver."
    p = T.with_policy(custom)
    assert p.startswith(custom)                                           # agent'a özel talimat korunur, en başta
    assert p.count(STYLE_MARK) == 1 and p.count("TERMINOLOGY POLICY") == 1
    assert p.index(custom) < p.index(STYLE_MARK) < p.index("TERMINOLOGY POLICY")
    assert p.rstrip().endswith(T.POLICY_PROMPT.strip())                    # çelişkide son söz terminology policy'de
    assert T.with_policy(p) == p                                           # idempotent
    assert SYSTEM_EXPLAIN.count(STYLE_MARK) == 1


def test_style_rules_cover_the_contract_and_follow_terminology():
    assert T.find_violations(STYLE_PROMPT) == []
    assert T.find_violations(SYSTEM_EXPLAIN_BASE) == []
    for must in ("İlk cümlede", "Ölçüm:", "Hipotez:", "Doğrulanmış neden:", "ölçülen sıfır", "veri yok",
                 "başarısız query", "Risk score", "kısa soruya kısa yanıt", "şema"):
        assert must in STYLE_PROMPT, must
    # veri sözleşmeleri analiz prompt'unda yerinde: veri bloğu talimat değildir, evidence ID'leri, dört ayrı kavram
    for must in ("VERİDİR", "ev-...", "DÖRT AYRI", "UYDURMA", "true_zero_signals", "data_gaps"):
        assert must in SYSTEM_EXPLAIN_BASE, must
    assert "400 kelime" not in SYSTEM_EXPLAIN_BASE and "Madde işareti ve tablo KULLANMA" not in SYSTEM_EXPLAIN_BASE


class _Capture(FakeLlm):
    def explain(self, analysis, question=None):
        self.payload = analysis
        return super().explain(analysis, question)


def test_explain_payload_distinguishes_measured_zero_from_failed_query(settings):  # noqa: F811
    """Modele giden veri üç durumu ayrı taşır: ölçülen sıfır, veri yok, yanıt vermeyen query. Skor değişmez."""
    llm = _Capture()
    st, _ = _run(settings, scenario="normal", gw=_with_failed("normal", {"jvm_gc": "TIMEOUT"}), llm=llm)
    r = _report(st)
    p = llm.payload
    assert {"edges", "lock_waiters"} <= set(p["true_zero_signals"])        # collector yazdı, kayıt yok → ölçülen 0
    assert any(g.startswith("jvm_gc: TIMEOUT") for g in p["data_gaps"])     # yanıt vermeyen query
    assert not any(g.startswith(("edges:", "lock_waiters:")) for g in p["data_gaps"])
    assert p["risk"] == r["risk"] and p["status"] == "partial"             # skor motordan; prompt değiştirmez

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
                 "başarısız query", "Risk score", "Varsayılan olarak ayrıntılı", "kısa yanıt isterse",
                 "önceki dönem", "Karşılaştırma verisi yoksa", "Normal seviye", "şema"):
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


def test_times_are_tsi_only_in_llm_data_and_deterministic_text(settings):  # noqa: F811
    """Kullaniciya giden metin ve LLM veri blogu yalniz TSI tasir; rapor kaydi (API alani) UTC ISO kalir."""
    import json

    from airlab_agent import timefmt
    from airlab_agent.llm import data_json
    from airlab_agent.report import deterministic_explanation

    assert timefmt.tsi("2026-11-21T20:42:00Z") == "2026-11-21 23:42 TSİ"
    assert timefmt.tsi("2026-11-21T22:30:00Z") == "2026-11-22 01:30 TSİ"            # gun TSI'ye gore degisir
    assert timefmt.span("2026-11-21T12:15:00Z", "2026-11-21T12:20:00+00:00") == "2026-11-21 15:15–15:20 TSİ"
    src = {"windows": [{"name": "window", "start": "2026-11-21T12:15:00Z", "end": "2026-11-21T12:20:00Z"}],
           "dql": "fetch logs, from: \"2026-11-21T12:15:00Z\"", "n": 3, "note": "12:15"}
    out = json.loads(data_json(src))
    assert out["windows"][0] == {"name": "window", "start": "2026-11-21 15:15 TSİ", "end": "2026-11-21 15:20 TSİ"}
    assert out["dql"] == src["dql"] and out["n"] == 3 and out["note"] == "12:15"      # metin icindeki zamana dokunulmaz
    assert src["windows"][0]["start"].endswith("Z")                                   # girdi degismez

    llm = _Capture()
    st, _ = _run(settings, llm=llm)
    r = _report(st)
    assert r["windows"][0]["start"].endswith("Z")                                     # kayit UTC ISO kalir
    text = deterministic_explanation(r)
    assert "UTC" not in text and "TSİ" in text
    from airlab_agent.style import STYLE_PROMPT
    assert "TSİ" in STYLE_PROMPT


def test_user_text_names_periods_in_plain_language(settings):  # noqa: F811
    """Kullaniciya giden metin "pencere" / "baseline" yerine dogal ifade kullanir; veri alan adlari sozlesme olarak kalir."""
    from airlab_agent.report import deterministic_explanation

    llm = _Capture()
    st, _ = _run(settings, llm=llm)
    r = _report(st)
    assert {w["name"] for w in r["windows"]} >= {"window", "baseline"}                 # sema anahtarlari degismez
    text = deterministic_explanation(r)
    low = text.lower()
    assert "baseline" not in low and "pencere" not in low and "window" not in low, text
    assert "önceki dönem" in text
    assert T.find_violations(text) == []
    # analiz prompt'u alan adlarini karsiliklariyla esler ve eksik karsilastirma verisinde tahmini yasaklar
    for must in ('name: "window"', 'name: "baseline"', "karşılaştırma verisi yok", "Sonraki kontrol"):
        assert must in SYSTEM_EXPLAIN_BASE, must


def test_user_text_does_not_leak_internal_names(settings):  # noqa: F811
    """Uygulamanin ic adlari (mechanism, source, impact, signal, data gap ...) okura giden metne tasinmaz."""
    from airlab_agent.report import deterministic_explanation, gap_text, level_text, reason_text

    st, _ = _run(settings, llm=_Capture())
    r = _report(st)
    assert r["mechanisms"] and "mechanism" in r["mechanisms"][0]                       # sema anahtarlari degismez
    text = deterministic_explanation(r)
    assert T.find_internal(text) == [], [str(v) for v in T.find_internal(text)]
    assert "Tespit edilen problem" in text
    # ic adlarin karsiliklari her system prompt'a eklenir
    for must in ("mechanism →", "source →", "signal →", "data gap →", "yüzde olarak", "iç kodları"):
        assert must in STYLE_PROMPT, must
    assert [v.text.lower() for v in T.find_internal("Mechanism bulundu; baseline `window` alanı")] == ["mechanism", "baseline"]
    assert T.find_internal("shared resource pressure") == []                           # "resource" ic ad degildir
    assert level_text("high") == "yüksek" and level_text(None) == "—"
    assert gap_text("hikari: EMPTY — kaynak yazmadı") == "hikari (veri gelmedi; kaynak yazmadı)"
    assert reason_text("latency ×3.0 (http_mean, +40 ms)") == "latency ×3.0 (ortalama, +40 ms)"

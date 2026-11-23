"""Terminology policy: açıklama dili Türkçe, teknik terimler İngilizce. Policy verisi tek yerde (terminology.yaml);
prompt'lar, deterministik rapor metinleri ve kaynak kodundaki kullanıcıya görünen string'ler buna göre denetlenir."""
from pathlib import Path

import pytest

from airlab_agent import terminology as T
from airlab_agent.config import Settings
from airlab_agent.graph import run_analysis
from airlab_agent.llm import SYSTEM_EXPLAIN, SYSTEM_EXPLAIN_BASE
from airlab_agent.report import deterministic_explanation, to_markdown
from scenarios import SCENARIOS
from synth import FakeGateway, build, cfg, windows

PKG = Path(T.__file__).parent

REQUIRED_TERMS = ["RCA", "Incident Analysis", "Root Cause", "Bottleneck", "Deadlock", "Lock Wait", "Thread Pool",
                  "Connection Pool", "Error Budget", "SLO", "SLI", "Consumer Lag", "Heap Dump", "Garbage Collection",
                  "Circuit Breaker", "Timeout", "Retry", "Rollback"]
REQUIRED_DOMAINS = ["SRE", "DevOps", "Linux", "Kubernetes", "PostgreSQL", "JVM", "Kafka", "Dynatrace", "Observability",
                    "Networking", "Cloud", "Distributed Systems"]


def _terms(v):
    return [x.text.lower() for x in v]


# ---- policy içeriği -------------------------------------------------------------------------------------------------

def test_policy_names_every_required_term_and_domain():
    for t in REQUIRED_TERMS:
        assert t in T.POLICY_PROMPT, t
    for d in REQUIRED_DOMAINS:
        assert d in T.POLICY_PROMPT, d


def test_policy_is_open_ended_and_protects_raw_values():
    p = T.POLICY_PROMPT
    assert "ÖRNEKTİR" in p and "listede" in p                          # sabit listeye bağlı değil
    for kw in ("log satırları", "metric", "span", "exception", "API field", "SQL keyword", "DQL expression",
               "entity", "error code"):
        assert kw in p, kw
    assert "parantez içinde" in p                                        # açıklama gerekiyorsa terim + (Türkçe)


def test_every_calque_maps_to_an_english_term():
    for c in T.calques():
        assert c.term and not T.find_violations(c.term), c.term


def test_every_calque_example_is_detected():
    """yaml'daki okunur örnek (tr) kendi kalıbına (veya daha özel bir kalıba) takılmalı: veri ile regex tutarlı."""
    for c in T._data()["calques"]:
        v = T.find_violations(c["tr"])
        assert v and v[0].start == 0, c


# ---- detector -------------------------------------------------------------------------------------------------------

def test_detector_flags_literal_translations():
    bad = "Veritabanındaki kilit kenarları nedeniyle iş parçacıkları bekliyor."
    assert _terms(T.find_violations(bad)) == ["kilit kenarları", "iş parçacıkları"]
    v = T.find_violations("Bağlantı havuzu doldu, zaman aşımı ve yeniden deneme arttı; kök neden belirsiz.")
    assert [x.term for x in v] == ["Connection Pool", "Timeout", "Retry", "Root Cause"]
    assert [x.term for x in T.find_violations("KİLİT beklemesi ve GECİKME")] == ["Lock Wait", "latency"]


def test_detector_accepts_target_style():
    good = ("PostgreSQL üzerinde Lock Wait oluşmuş. Bazı transaction'lar diğer transaction'ların tuttuğu "
            "lock'ların serbest bırakılmasını bekliyor. Bu durum Query Latency değerlerini artırıyor.")
    assert T.find_violations(good) == []
    assert T.find_violations("Consumer Lag (consumer'ın son mesajın gerisinde kalma miktarı) güvenli seviyede.") == []
    assert T.find_violations("İlişki kanıtlanamadı.") == []                 # fiil (prove) serbest
    assert [x.term for x in T.find_violations("Kanıtlar yetersiz.")] == ["evidence"]   # isim değil


def test_detector_ignores_code_and_data_regions():
    txt = ("Log satırı: `ERROR: kilit alınamadı` ve DQL `fetch logs | filter contains(content, \"gecikme\")`.\n"
           "```\nkök neden: bilinmiyor\n```\n<analysis_data>{\"msg\": \"bağlantı havuzu\"}</analysis_data>")
    assert T.find_violations(txt) == []


# ---- prompt katmanları ----------------------------------------------------------------------------------------------

def test_user_facing_prompt_carries_policy_once_and_base_is_clean():
    assert SYSTEM_EXPLAIN.count("TERMINOLOGY POLICY") == 1
    assert SYSTEM_EXPLAIN.rstrip().endswith(T.POLICY_PROMPT.strip())
    assert T.with_policy(SYSTEM_EXPLAIN) == SYSTEM_EXPLAIN               # idempotent
    assert T.find_violations(SYSTEM_EXPLAIN_BASE) == []                 # çelişen eski Türkçe terimler kalmadı


# ---- deterministik çıktılar (Claude yokken kullanıcıya giden metin) ---------------------------------------------------

@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_deterministic_report_and_markdown_follow_policy(tmp_path, scenario):
    ws = windows()
    base, win, extra = SCENARIOS[scenario]()
    gw = FakeGateway(build(base, win, ws, **extra))
    st = run_analysis(Settings(data_dir=tmp_path, cfg=cfg()), lambda: gw, ws[1].start, ws[1].end,
                      llm=None, use_llm=False, fixture=True)
    import json
    r = json.loads(Path(st["report_paths"][0]).read_text(encoding="utf-8"))
    for name, text in (("explanation", deterministic_explanation(r)), ("markdown", to_markdown(r))):
        v = T.find_violations(text)
        assert not v, f"{scenario}/{name}: {', '.join(map(str, v))}"


# ---- kaynak kodu lint'i -------------------------------------------------------------------------------------------------

def test_agent_source_strings_follow_policy():
    hits = []
    for p in sorted(PKG.glob("*.py")):
        if p.name == "terminology.py":                                   # policy'nin kendisi yanlış örnekleri içerir
            continue
        hits += [f"{p.name}:{ln}: {v}" for ln, _, v in T.scan_python_source(p.read_text(encoding="utf-8"))]
    assert not hits, "\n".join(hits)


def test_source_lint_respects_allow_mark():
    src = 'a = "kilit"\nb = ("kilit "  # term-ok\n     "ve gecikme")\n'
    assert [(ln, v.term) for ln, _, v in T.scan_python_source(src)] == [(1, "lock")]
    ts = 'const a = "Kök neden";\nconst b = "kilit"; // term-ok\n<p>Bağlantı havuzu</p>\n'
    assert [(ln, v.term) for ln, _, v in T.scan_ts_source(ts)] == [(1, "Root Cause"), (3, "Connection Pool")]

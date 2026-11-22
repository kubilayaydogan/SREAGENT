"""Zincirleme kilit: kök engelleyiciden başlayan halka sırası (PostgreSQL üzerinden yayılan etki)."""
from airlab_agent.attribution import chain_text, lock_chain
from airlab_agent.report import deterministic_explanation


def E(w, b, n, bw="Lock"):
    return {"waiter_app": w, "blocker_app": b, "n": n, "blocker_wait": bw}


EDGES = [
    E("checkin-service", "reservation-service", 120, bw="Timeout"),   # kök: reservation (kendisi kilit beklemiyor)
    E("baggage-service", "checkin-service", 80),                      # checkin beklerken baggage'ı bekletiyor
    E("flightops-service", "baggage-service", 40),
    E("checkin-service", "checkin-service", 30),                      # uygulama içi bekleme zinciri oluşturmaz
]


def test_chain_levels_from_root():
    ch = lock_chain(["reservation-service"], EDGES)
    assert [(h["app"], h["level"]) for h in ch] == [
        ("reservation-service", 0), ("checkin-service", 1), ("baggage-service", 2), ("flightops-service", 3)]
    assert ch[2]["waits_on"] == [{"app": "checkin-service", "n": 80}]
    assert chain_text(ch) == "reservation-service → checkin-service → baggage-service → flightops-service"


def test_single_hop_and_empty():
    assert chain_text(lock_chain(["reservation-service"], EDGES[:1])) == "reservation-service → checkin-service"
    assert lock_chain([], EDGES) == []
    assert lock_chain(["reservation-service"], [E("a", "a", 5)]) == []


def test_branching_same_level():
    edges = [E("checkin-service", "reservation-service", 10, "Timeout"), E("flightops-service", "reservation-service", 5, "Timeout")]
    assert chain_text(lock_chain(["reservation-service"], edges)) == \
        "reservation-service → checkin-service + flightops-service"


def test_deterministic_report_is_paragraphs_with_chain():
    mech = {"id": "row_lock", "title": "Zincirleme satır kilidi beklemesi", "confidence": "high",
            "source_apps": ["reservation-service"],
            "affected_apps": ["checkin-service", "baggage-service", "flightops-service"],
            "mechanism": "Kaynak uygulamanın açık transaction'ı satır kilidini tutuyor.",
            "signals": ["blocking_edge logları"], "evidence_ids": ["ev-1"], "edges_total": 270,
            "lock_chain": lock_chain(["reservation-service"], EDGES), "chain_depth": 3,
            "chain_path": "reservation-service → checkin-service → baggage-service → flightops-service",
            "cascade": "Etki zincir boyunca yayılıyor."}
    r = {"risk": {"overall": {"score": 78.0, "band": "yüksek"}, "apps": {"checkin-service": {}, "baggage-service": {}}},
         "causality": {"primary_mechanism": "row_lock", "confidence": "high"},
         "windows": [{"name": "baseline", "start": "2026-11-21T20:30:00Z", "end": "2026-11-21T20:45:00Z"},
                     {"name": "window", "start": "2026-11-21T20:45:00Z", "end": "2026-11-21T21:00:00Z"}],
         "mechanisms": [mech], "impacted": {"checkin-service": {"reasons": ["gecikme ×12.0 (span_p95, +3000 ms)"]}},
         "data_coverage": {"signals_ok": 15, "signals_total": 15}, "data_gaps": [], "alternatives": [],
         "human_review": ["Kaynak transaction'ları inceleyin."]}
    txt = deterministic_explanation(r)
    paras = txt.split("\n\n")
    assert [p.split(".**")[0] for p in paras] == ["**Özet", "**Etki", "**Kök neden ve yayılım", "**Kanıt ve güven",
                                                  "**Önerilen inceleme"]
    assert "- " not in txt                                           # madde işareti yok
    assert "20:45 UTC (23:45 TSİ)" in paras[0] and "78.0/100" in paras[0]
    assert "baggage-service, checkin-service uygulamasını 80 kez bekledi" in paras[2]
    assert "3 halka" in paras[2]

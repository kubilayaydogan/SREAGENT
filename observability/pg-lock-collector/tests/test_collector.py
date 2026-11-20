import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector import DeltaTracker, connection_capacity, metric_line, parse_sql_tag  # noqa: E402


def test_parse_sql_tag_full():
    q = "/* app=reservation-service op=create_booking rid=r-1 exp=exp-20261120-ab12 */ UPDATE flightops.flight_inventory"
    assert parse_sql_tag(q) == {"app": "reservation-service", "op": "create_booking", "rid": "r-1",
                                "exp": "exp-20261120-ab12"}


def test_parse_sql_tag_lab_and_partial():
    q = "/* app=checkin-service op=lab_hold_checkin rid=x */ /* lab:hold-inventory-lock */ SELECT pg_sleep($1)"
    t = parse_sql_tag(q)
    assert t["lab"] == "hold-inventory-lock" and t["op"] == "lab_hold_checkin" and "exp" not in t
    assert parse_sql_tag(None) == {}
    assert parse_sql_tag("SELECT 1") == {}


def test_metric_line_gauge_and_count_and_sanitize():
    assert metric_line("airlab.pg.x", {"b": "v", "a": 1}, 3, ts_ms=1000) == 'airlab.pg.x,a="1",b="v" gauge,3 1000'
    assert metric_line("k", {}, 2.5, kind="count") == "k count,delta=2.5"
    assert metric_line("k", {"q": 'a"b\nc'}, 1) == 'k,q="a_b_c" gauge,1'


def test_delta_tracker_first_normal_reset_and_decrease():
    d = DeltaTracker()
    assert d.update(1, {("a",): 10.0}) == ({}, "first_sample")
    assert d.update(1, {("a",): 15.0}) == ({("a",): 5.0}, None)
    # yeni anahtar
    deltas, why = d.update(1, {("a",): 15.0, ("b",): 2.0})
    assert why is None and deltas == {("a",): 0.0, ("b",): 2.0}
    # reset epoch değişti → delta yok
    assert d.update(2, {("a",): 1.0, ("b",): 0.0}) == ({}, "reset_or_restart")
    assert d.update(2, {("a",): 4.0, ("b",): 0.0}) == ({("a",): 3.0, ("b",): 0.0}, None)
    # aynı epoch'ta geri gidiş → delta yok (ör. pg_stat_statements eviction)
    assert d.update(2, {("a",): 1.0, ("b",): 0.0}) == ({}, "counter_decreased")


def test_connection_capacity_uses_normal_slots_denominator():
    c = connection_capacity(100, 3, 3, 47)
    assert c["normal_slots"] == 94 and c["used_ratio_normal"] == round(47 / 94, 4)

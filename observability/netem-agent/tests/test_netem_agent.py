import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
import ipaddress

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netem_agent as na  # noqa: E402

TARGETS = na.parse_targets("db=10.0.0.5:5432,checkin=checkin")


def make_agent(clock=None):
    cmds = []
    a = na.Agent(TARGETS, "eth0", runner=cmds.append, clock=clock or (lambda: 1000.0),
                 resolver=lambda h: {"10.0.0.5": "10.0.0.5", "checkin": "172.28.0.12"}[h])
    return a, cmds


def test_parse_targets():
    assert TARGETS["db"] == na.Target("db", "10.0.0.5", 5432)
    assert TARGETS["checkin"].port is None
    with pytest.raises(ValueError):
        na.parse_targets("bad")


@pytest.mark.parametrize("body,msg", [
    ({"target": "evil", "delayMs": 10}, "allowlist"),
    ({"target": "db", "delayMs": 5000}, "delayMs"),
    ({"target": "db", "lossPct": 80}, "lossPct"),
    ({"target": "db"}, "> 0"),
    ({"target": "db", "delayMs": 10, "jitterMs": 50}, "jitterMs"),
    ({"target": "db", "delayMs": 10, "ttlSec": 10_000}, "ttlSec"),
    ({"target": "db", "delayMs": "x"}, "sayı"),
])
def test_validation_rejects(body, msg):
    with pytest.raises(na.ValidationError, match=msg):
        na.validate(body, TARGETS)


def test_apply_builds_scoped_tc_commands():
    a, cmds = make_agent()
    a.apply({"target": "db", "delayMs": 150, "jitterMs": 20, "lossPct": 2, "ttlSec": 60})
    flat = [" ".join(c) for c in cmds]
    assert flat[0] == "tc qdisc del dev eth0 root"
    assert "prio bands 4" in flat[1]
    assert flat[2].endswith("netem delay 150ms 20ms distribution normal loss 2%")
    assert "match ip dst 10.0.0.5/32 match ip dport 5432 0xffff flowid 1:4" in flat[3]
    # hiçbir komut shell üzerinden çalışmaz: liste argüman
    assert all(isinstance(c, list) for c in cmds)


def test_multiple_targets_get_separate_bands_and_clear():
    a, cmds = make_agent()
    a.apply({"target": "db", "delayMs": 50})
    cmds.clear()
    a.apply({"target": "checkin", "lossPct": 5})
    flat = [" ".join(c) for c in cmds]
    assert "prio bands 5" in flat[1]
    assert any("dst 172.28.0.12/32 flowid 1:5" in f for f in flat)
    cmds.clear()
    assert a.clear("db") == 1
    assert [r["target"] for r in a.status()["rules"]] == ["checkin"]
    assert a.clear() == 1
    assert cmds[-1] == ["tc", "qdisc", "del", "dev", "eth0", "root"]


def test_ttl_expiry_removes_rule():
    now = [1000.0]
    a, cmds = make_agent(clock=lambda: now[0])
    a.apply({"target": "db", "delayMs": 50, "ttlSec": 30})
    now[0] += 29
    assert a.expire() == 0
    now[0] += 2
    assert a.expire() == 1
    assert a.status()["rules"] == []
    assert cmds[-1] == ["tc", "qdisc", "del", "dev", "eth0", "root"]


def test_failed_tc_rolls_back_rule():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        if "netem" in cmd and "loss" in cmd:
            raise RuntimeError("boom")

    a = na.Agent(TARGETS, "eth0", runner=runner, resolver=lambda h: "10.0.0.5")
    with pytest.raises(RuntimeError):
        a.apply({"target": "db", "lossPct": 5})
    assert a.status()["rules"] == []


@pytest.fixture
def server():
    a, _ = make_agent()
    h = na.make_handler(a, "x" * 20, [ipaddress.ip_network("127.0.0.1/32")])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(url, method="GET", body=None, token="x" * 20):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"X-Lab-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_requires_token(server):
    assert call(server + "/healthz", token=None)[0] == 200
    assert call(server + "/netem", token=None)[0] == 401
    assert call(server + "/netem", token="y" * 20)[0] == 401


def test_http_apply_status_delete(server):
    code, rule = call(server + "/netem", "POST", {"target": "db", "delayMs": 100})
    assert code == 200 and rule["delay_ms"] == 100
    assert call(server + "/netem", "POST", {"target": "nope", "delayMs": 1})[0] == 400
    assert len(call(server + "/netem")[1]["rules"]) == 1
    assert call(server + "/netem", "DELETE")[1] == {"removed": 1}


def test_http_rejects_non_allowlisted_source():
    a, _ = make_agent()
    h = na.make_handler(a, "x" * 20, [ipaddress.ip_network("172.28.0.50/32")])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert call(f"http://127.0.0.1:{srv.server_address[1]}/netem")[0] == 403
    finally:
        srv.shutdown()

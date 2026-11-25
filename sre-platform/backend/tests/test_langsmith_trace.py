"""LangSmith: sonuç değerleri feedback olarak yazılır, trace işaretlenir, link yalnız trace varsa üretilir."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from conftest import run
from sre_platform.db.models import InvestigationStatus, Severity, Trigger
from sre_platform.services import langsmith_trace as lt


class FakeClient:
    def __init__(self, fail=False):
        self.feedback, self.fail, self.flushed = [], fail, 0

    def flush(self):
        self.flushed += 1

    def create_feedback(self, run_id, key, **kw):
        if self.fail:
            raise ConnectionError("down")
        self.feedback.append((run_id, key, kw))

    def read_project(self, project_name):
        return SimpleNamespace(id=uuid.UUID(int=7))

    def get_run_url(self, run):
        return f"https://smith.test/o/t1/projects/p/{run.session_id}/r/{run.id}?poll=true"


class FakeTracer:
    project_name = "airlab-test"

    def __init__(self, client):
        self.client, self.waited = client, 0

    def wait_for_futures(self):
        self.waited += 1


def test_outcome_feedback_and_link(settings):
    from sre_platform.db.session import make_engine, make_sessionmaker
    from sre_platform.repositories import investigations
    ok, bad = FakeClient(), FakeClient(fail=True)

    async def go():
        eng = make_engine(settings, "pytest")
        sm = make_sessionmaker(eng)
        rt = SimpleNamespace(sm=sm, callbacks=[FakeTracer(ok)])
        try:
            async with sm() as s, s.begin():
                inv = await investigations.create(s, run_id="ls1", trigger=Trigger.SCHEDULED, title="t",
                                                  window_start=datetime(2026, 11, 25, 10, tzinfo=timezone.utc),
                                                  window_end=datetime(2026, 11, 25, 10, 15, tzinfo=timezone.utc))
                inv.status, inv.risk_score, inv.severity = InvestigationStatus.PARTIAL, Decimal("72.5"), Severity.HIGH
                inv.primary_mechanism, inv.data_coverage = "row_lock", Decimal("0.8")
                other = await investigations.create(s, run_id="ls2", trigger=Trigger.SCHEDULED, title="eski",
                                                    window_start=datetime(2026, 11, 25, 10, tzinfo=timezone.utc),
                                                    window_end=datetime(2026, 11, 25, 10, 15, tzinfo=timezone.utc))
                iid, oid = inv.id, other.id
            r_ok = await lt.record_outcome(rt, iid)
            r_bad = await lt.record_outcome(SimpleNamespace(sm=sm, callbacks=[FakeTracer(bad)]), oid)
            r_off = await lt.record_outcome(SimpleNamespace(sm=sm, callbacks=[]), iid)
            async with sm() as s:
                marked = (await lt.has_trace(s, iid), await lt.has_trace(s, oid))
            url = await lt.run_url(rt, iid)
            off_url = await lt.run_url(SimpleNamespace(callbacks=[]), iid)
            return iid, r_ok, r_bad, r_off, marked, url, off_url
        finally:
            await eng.dispose()

    iid, r_ok, r_bad, r_off, marked, url, off_url = run(go)
    assert (r_ok, r_bad, r_off) == (True, False, False)
    got = {k: kw for rid, k, kw in ok.feedback if rid == iid}
    assert got["risk_score"] == {"score": 72.5} and got["mechanism"] == {"value": "row_lock"}
    assert got["severity"] == {"value": "high"} and got["partial_data"] == {"score": 1}
    assert got["data_coverage"] == {"score": 0.8} and ok.flushed == 1
    assert marked == (True, True)                       # feedback başarısız olsa da trace yazıldı
    assert url == f"https://smith.test/o/t1/projects/p/{uuid.UUID(int=7)}/r/{iid}?poll=true"
    assert off_url is None

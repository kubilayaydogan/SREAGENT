"""Slack bildirimi: eşik, mesaj biçimi, tekrar göndermeme ve hata yalıtımı."""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from conftest import run
from sre_platform.db.models import (Confidence, Investigation, InvestigationStatus, Severity, ToolExecution, ToolStatus,
                                    Trigger)
from sre_platform.services import slack_notify as sn

HOOK = "https://hooks.slack.com/services/T000/B000/secretsecret"


def _inv(**kw) -> Investigation:
    base = dict(id=uuid.uuid4(), run_id="r1", trigger=Trigger.CHAT, title="checkin yavaşladı",
                status=InvestigationStatus.COMPLETED, severity=Severity.CRITICAL, risk_score=Decimal("81.0"),
                confidence=Confidence.MEDIUM, primary_mechanism="row_lock", source_apps=["flightops-service"],
                affected_apps=["checkin-service", "reservation-service"],
                window_start=datetime(2026, 11, 24, 21, 27, tzinfo=timezone.utc),
                window_end=datetime(2026, 11, 24, 21, 31, tzinfo=timezone.utc),
                explanation="## Özet\n**Root Cause:** flightops uzun transaction. [Doc](https://x.test/a)",
                explanation_source="Claude (claude-opus-5-5)")
    base.update(kw)
    return Investigation(**base)


def test_threshold_and_status():
    assert sn.should_notify(_inv(), 75)
    assert not sn.should_notify(_inv(risk_score=Decimal("74.9")), 75)
    assert not sn.should_notify(_inv(risk_score=None), 75)
    assert not sn.should_notify(_inv(status=InvestigationStatus.FAILED), 75)
    assert sn.should_notify(_inv(status=InvestigationStatus.PARTIAL), 75)


def test_message_format():
    inv = _inv()
    m = sn.build_message(inv, "http://ui.test")
    flat = str(m)
    assert "risk 81 (critical)" in m["text"] and f"http://ui.test/investigations/{inv.id}" in flat
    assert "2026-11-25 00:27–00:31 İstanbul" in flat                     # yerel saat
    assert "*Root Cause:*" in flat and "<https://x.test/a|Doc>" in flat     # markdown → mrkdwn
    assert "row_lock" in flat and "flightops-service" in flat and "claude-opus-5-5" in flat
    long = sn.build_message(_inv(explanation="x" * 9000), "http://ui.test")
    assert all(len(b["text"]["text"]) <= 3000 for b in long["blocks"] if b.get("type") == "section" and "text" in b)


def test_notify_sends_once_and_isolates_errors(settings, monkeypatch):
    from sre_platform.db.session import make_engine, make_sessionmaker
    from sre_platform.repositories import investigations
    sent: list[dict] = []
    fail = {"on": False}

    async def fake_post(url, payload, attempts=3):
        assert url == HOOK
        if fail["on"]:
            raise RuntimeError(f"HTTP 404: no_service {HOOK}")
        sent.append(payload)
    monkeypatch.setattr(sn, "_post", fake_post)

    async def go():
        eng = make_engine(settings, "pytest")
        sm = make_sessionmaker(eng)
        rt = SimpleNamespace(ps=dataclasses.replace(settings, slack_webhook_url=HOOK, slack_risk_threshold=75.0), sm=sm)
        try:
            async with sm() as s, s.begin():
                hi = await investigations.create(s, run_id="hi", trigger=Trigger.SCHEDULED, title="yüksek",
                                                 window_start=datetime(2026, 11, 25, 10, tzinfo=timezone.utc),
                                                 window_end=datetime(2026, 11, 25, 10, 15, tzinfo=timezone.utc))
                lo = await investigations.create(s, run_id="lo", trigger=Trigger.SCHEDULED, title="düşük",
                                                 window_start=datetime(2026, 11, 25, 10, tzinfo=timezone.utc),
                                                 window_end=datetime(2026, 11, 25, 10, 15, tzinfo=timezone.utc))
                for inv, score in ((hi, "88"), (lo, "40")):
                    inv.status, inv.risk_score, inv.severity = InvestigationStatus.COMPLETED, Decimal(score), Severity.HIGH
                hi_id, lo_id = hi.id, lo.id
            fail["on"] = True
            r_fail = await sn.notify_rca(rt, hi_id)                      # hata → False, exception yok
            fail["on"] = False
            r1 = await sn.notify_rca(rt, hi_id)                          # başarısız denemeden sonra tekrar denenir
            r2 = await sn.notify_rca(rt, hi_id)                          # başarılı gönderimden sonra tekrar yok
            r_lo = await sn.notify_rca(rt, lo_id)                        # eşik altı
            off = await sn.notify_rca(SimpleNamespace(ps=settings, sm=sm), hi_id)   # webhook tanımsız → kapalı
            async with sm() as s:
                tools = (await s.scalars(select(ToolExecution).where(ToolExecution.tool_name == sn.TOOL))).all()
            return r_fail, r1, r2, r_lo, off, tools
        finally:
            await eng.dispose()

    r_fail, r1, r2, r_lo, off, tools = run(go)
    assert (r_fail, r1, r2, r_lo, off) == (False, True, False, False, False)
    assert len(sent) == 1 and "yüksek" in sent[0]["text"]
    st = sorted(t.execution_status for t in tools)
    assert st == sorted([ToolStatus.FAILED, ToolStatus.SUCCEEDED])
    assert all(HOOK not in (t.error_message or "") for t in tools)          # webhook adresi kayda yazılmaz

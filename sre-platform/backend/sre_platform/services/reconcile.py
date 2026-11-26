"""Yarıda kalan çalıştırmaların kapatılması.

Süreç bir çalıştırmanın ortasında durursa (yeniden başlatma, çökme) kayıt "çalışıyor" durumunda kalır: arayüz bitmeyen
bir çalıştırma gösterir, eşzamanlı çalıştırma sayımı şişer. Bu modül, kendi süre sınırını açıkça aşmış kayıtları
"başarısız" olarak kapatır ve nedenini yazar. Sonuç uydurulmaz; rapor üretilmez.

Sınırlar süre tabanlıdır, bu yüzden backend ve worker aynı anda çalışırken de güvenlidir: süren bir çalıştırma kendi
timeout'undan önce bu sınıra ulaşamaz. Sınır = ilgili süre sınırı + GRACE_S.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (AgentRun, Investigation, InvestigationStatus, JobRun, JobStatus, ToolExecution, ToolStatus)
from ..repositories import investigations

log = logging.getLogger(__name__)

GRACE_S = 300
MIN_INVESTIGATION_S = 600           # ayar sonradan küçültülmüş olabilir: eski koşular için alt sınır
AGENT_RUN_MAX_S = 1800              # builder/definition.py Limits.timeout_s üst sınırı
INTERRUPTED = ("Çalıştırma yarıda kesildi (süreç yeniden başlatıldı ya da süre sınırı aşıldı). Sonuç üretilmedi; "
               "yeniden çalıştırabilirsiniz.")


async def close_interrupted(s: AsyncSession, *, run_timeout_s: int, lease_s: int,
                            now: datetime | None = None) -> dict[str, int]:
    """Süre sınırını aşmış 'çalışıyor' kayıtlarını kapatır. Dönüş: tür başına kapatılan kayıt sayısı."""
    now = now or datetime.now(timezone.utc)
    out = {"investigations": 0, "tool_executions": 0, "job_runs": 0, "agent_runs": 0}

    cut = now - timedelta(seconds=max(int(run_timeout_s), MIN_INVESTIGATION_S) + GRACE_S)
    stale = list((await s.execute(
        select(Investigation).where(Investigation.status.in_([InvestigationStatus.PENDING, InvestigationStatus.RUNNING]),
                                    Investigation.created_at < cut).with_for_update(skip_locked=True))).scalars())
    for inv in stale:
        if (inv.started_at or inv.created_at) >= cut:
            continue
        await investigations.fail(s, inv, INTERRUPTED)
        open_tools = list((await s.execute(select(ToolExecution).where(
            ToolExecution.investigation_id == inv.id, ToolExecution.execution_status == ToolStatus.STARTED))).scalars())
        for t in open_tools:
            await investigations.tool_finished(s, t, ok=False, error="sonuç alınamadı (çalıştırma yarıda kesildi)")
        out["investigations"] += 1
        out["tool_executions"] += len(open_tools)

    res = await s.execute(update(JobRun).where(
        JobRun.status == JobStatus.RUNNING, JobRun.started_at < now - timedelta(seconds=int(lease_s) + GRACE_S)
    ).values(status=JobStatus.FAILED, finished_at=now, error=INTERRUPTED))
    out["job_runs"] = res.rowcount or 0

    res = await s.execute(update(AgentRun).where(
        AgentRun.status == "running", AgentRun.started_at < now - timedelta(seconds=AGENT_RUN_MAX_S + GRACE_S)
    ).values(status="failed", finished_at=now, error_code="interrupted", error=INTERRUPTED))
    out["agent_runs"] = res.rowcount or 0

    if any(out.values()):
        log.warning("yarıda kalan çalıştırmalar kapatıldı", extra={"closed": out})
    return out

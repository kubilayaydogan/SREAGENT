"""Tohum kayıtlar (bilgi bankası) ve eski airlab-agent JSON raporlarının içe aktarımı. Her ikisi de idempotenttir."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..core.config import PlatformSettings
from ..db.models import Trigger
from ..db.session import make_engine, make_sessionmaker, transaction
from ..repositories import episodes, investigations, knowledge

log = logging.getLogger("sre.seed")
SEED_DIR = Path(__file__).parent


def load_seed() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list] = {"known_issues": [], "runbooks": []}
    for p in sorted(SEED_DIR.glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k in out:
            out[k] += [{**x, "origin": "seed"} for x in d.get(k, [])]
    return out


async def seed_knowledge(s) -> dict[str, int]:
    return await knowledge.seed(s, load_seed())


async def run_seed(ps: PlatformSettings) -> dict[str, int]:
    eng = make_engine(ps, "sre-seed")
    try:
        async with transaction(make_sessionmaker(eng)) as s:
            return await seed_knowledge(s)
    finally:
        await eng.dispose()


async def backfill_episodes(ps: PlatformSettings) -> int:
    """Episodic memory backfill: episode'u olmayan tamamlanmış investigation'lar (idempotent)."""
    eng = make_engine(ps, "sre-episodes")
    try:
        async with transaction(make_sessionmaker(eng)) as s:
            return await episodes.backfill(s)
    finally:
        await eng.dispose()


def _ts(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


async def import_report(s, r: dict[str, Any]) -> bool:
    """Tek JSON raporu araştırma kaydı olarak yazar (run_id ile idempotent). Rapor içeriği değiştirilmez."""
    run_id = str(r.get("id") or "")
    if not run_id or await investigations.by_run_id(s, run_id):
        return False
    wins = {w["name"]: w for w in r.get("windows") or [] if isinstance(w, dict)}
    win = wins.get("window")
    if not win:
        log.warning("pencere bilgisi olmayan rapor atlandı", extra={"run_id": run_id})
        return False
    base = wins.get("baseline")
    kind = r.get("kind", "analyze")
    title = {"watch-incident": "İçe aktarılan watch olayı analizi", "watch-recovery": "İçe aktarılan recovery analizi"}.get(
        kind, "İçe aktarılan analiz") + f" ({run_id})"
    inv = await investigations.create(s, run_id=run_id, trigger=Trigger.IMPORT, title=title,
                                      window_start=_ts(win["start"]), window_end=_ts(win["end"]),
                                      baseline=(_ts(base["start"]), _ts(base["end"])) if base else None,
                                      description=r.get("question") or "; ".join(r.get("notes") or []) or None)
    inv.started_at = _ts(r.get("generated_at"))
    await investigations.complete(s, inv, r)
    gen = _ts(r.get("generated_at"))
    if gen:
        inv.created_at = gen
        inv.completed_at = gen
        inv.duration_ms = None                     # eski raporda süre bilgisi yok
    await s.flush()
    return True


async def import_reports(ps: PlatformSettings, directory: str) -> int:
    eng = make_engine(ps, "sre-import")
    n = 0
    try:
        sm = make_sessionmaker(eng)
        for p in sorted(Path(directory).glob("*.json")):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                log.warning("okunamayan rapor", extra={"file": p.name, "err": str(e)[:200]})
                continue
            async with transaction(sm) as s:
                if await import_report(s, r):
                    n += 1
    finally:
        await eng.dispose()
    return n

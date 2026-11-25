"""Watch testleri için bellek içi durum deposu (platformdaki PgStateStore ile aynı arayüz)."""
from __future__ import annotations

import copy
from datetime import datetime, timezone


class MemStore:
    def __init__(self):
        self.kv: dict = {}
        self.cond: dict = {}
        self.hist: dict = {}
        self.incidents: dict = {}
        self.events: list[tuple] = []

    def get(self, k, default=None):
        return copy.deepcopy(self.kv.get(k, default))

    def put(self, k, v):
        self.kv[k] = copy.deepcopy(v)

    def conds(self) -> dict:
        return copy.deepcopy(self.cond)

    def put_cond(self, k, v):
        self.cond[k] = copy.deepcopy(v)

    def history(self, n: int) -> list[dict]:
        return [copy.deepcopy(self.hist[e]) for e in sorted(self.hist)[-n:]] if n > 0 else []

    def add_hist(self, end: str, m: dict, keep: int):
        self.hist[end] = copy.deepcopy(m)
        for old in sorted(self.hist)[:-keep] if keep > 0 else list(self.hist):
            del self.hist[old]

    def event(self, kind, key, detail):
        self.events.append((datetime.now(timezone.utc).isoformat(), kind, key, copy.deepcopy(detail)))

    def open_incident(self):
        return next((copy.deepcopy(i) for i in self.incidents.values() if i["status"] == "open"), None)

    def save_incident(self, inc: dict):
        self.incidents[inc["id"]] = copy.deepcopy(inc)

    def commit(self):
        pass

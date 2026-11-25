"""LangSmith payload yakalama (test fixture'ı).

Gerçek `langsmith.Client` + `SafeTracer` kullanılır; yalnız ağ gönderimi (`_create_run` / `_update_run`) yakalanır.
Böylece hide_inputs / hide_outputs / hide_metadata ve hata sanitizasyonu GERÇEK gönderim yolunda doğrulanır.
"""
from __future__ import annotations

import json

from airlab_agent.tracing import make_client, make_tracer


class Capture:
    def __init__(self, project: str = "airlab-sre-agent"):
        self.creates: list[dict] = []
        self.updates: list[dict] = []
        self.feedback: list[dict] = []
        from langsmith import Client
        cap = self

        class CaptureClient(Client):                       # ağ yerine yakala (hide_* uygulandıktan SONRA)
            def _create_run(self, run, **kw):
                cap.creates.append(json.loads(json.dumps(run, default=str)))

            def _update_run(self, run, **kw):
                cap.updates.append(json.loads(json.dumps(run, default=str)))

            def create_feedback(self, run_id=None, key=None, **kw):     # sonuç feedback'i (ağ yok)
                cap.feedback.append({"run_id": str(run_id), "key": key, **{k: v for k, v in kw.items()
                                                                           if k in ("score", "value")}})
        client = make_client("lsv2_pt_" + "0" * 32, cls=CaptureClient, api_url="http://127.0.0.1:9",
                             auto_batch_tracing=False)
        self.client = client
        self.tracer = make_tracer(project, client)

    def runs(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for c in self.creates:
            out[str(c["id"])] = dict(c)
        for u in self.updates:
            out.setdefault(str(u["id"]), {}).update({k: v for k, v in u.items() if v is not None})
        return out

    def named(self, name: str) -> list[dict]:
        return [r for r in self.runs().values() if r.get("name") == name]

    def children(self, parent_id) -> list[dict]:
        return [r for r in self.runs().values() if str(r.get("parent_run_id")) == str(parent_id)]

    def roots(self) -> list[dict]:
        return [r for r in self.runs().values() if not r.get("parent_run_id")]

    def blob(self) -> str:
        return json.dumps(self.creates + self.updates, default=str, ensure_ascii=False)

    @staticmethod
    def meta(run: dict) -> dict:
        return (run.get("extra") or {}).get("metadata") or {}

"""Değerlendirme senaryoları — YALNIZ GİRDİ (telemetri biçiminde). Beklenen sonuçlar eval/labels.yaml'dadır ve
agent'a hiçbir aşamada verilmez. Kenar (edge) kayıtlarında experiment_id/lab_hold gibi kaos etiketleri bilerek
bulunur: agent bunları girişte silmeli ve kanıt olarak kullanmamalıdır.
"""
from __future__ import annotations

import copy

from synth import NORMAL


def _w(**over):
    w = copy.deepcopy(NORMAL)
    for k, v in over.items():
        if k == "apps":
            for app, vals in v.items():
                w["apps"][app].update(vals)
        elif k == "hosts":
            w["hosts"].update(v)
        else:
            w[k] = v
    return w


R, C, F, B = "reservation-service", "checkin-service", "flightops-service", "baggage-service"


def _edge(waiter, blocker, n, blocker_wait="Timeout"):
    return {"waiter_app": waiter, "blocker_app": blocker, "wait_type": "Lock", "waiter_op": "checkin",
            "blocker_op": "hold", "blocker_state": "active", "blocker_wait": blocker_wait, "n": str(n),
            "wait_max": "4.2", "waiter.experiment_id": "exp-LEAK", "blocker.lab_hold": "hold-inventory-lock"}


SCENARIOS = {
    "normal": lambda: (NORMAL, _w(), {}),
    "independent_load": lambda: (NORMAL, _w(apps={F: {"rpm": 200.0, "mean_ms": 22.0, "p95": 33.0}}), {}),
    "row_lock": lambda: (NORMAL, _w(
        apps={C: {"mean_ms": 900.0, "p95": 3900.0, "pool_a": 7.5, "pool_to": 1.0, "sessions": 8.0}},
        lock_total=6.0, lock_pairs={(C, R): 5.0, (C, C): 1.0},
        edges=[_edge(C, R, 140), _edge(C, C, 60, blocker_wait="Lock"), _edge(R, R, 10)], pg_lock_logs=900), {}),
    "single_pool": lambda: (NORMAL, _w(
        apps={B: {"rpm": 240.0, "pool_a": 8.0, "pool_pe": 4.0, "mean_ms": 300.0, "p95": 600.0}}), {}),
    "global_connection": lambda: (NORMAL, _w(
        conn_ratio=0.92, apps={R: {"sessions": 60.0, "mean_ms": 80.0, "p95": 120.0},
                               F: {"mean_ms": 70.0, "p95": 110.0}}), {}),
    "db_cpu": lambda: (NORMAL, _w(
        hosts={"db-vm": 96.0}, apps={C: {"exec_ms": 30000.0, "mean_ms": 200.0, "p95": 400.0},
                                     R: {"mean_ms": 90.0, "p95": 150.0}}), {}),
    "app_vm_cpu": lambda: (NORMAL, _w(
        hosts={"app-vm": 97.0}, apps={B: {"cores": 1.8}, R: {"mean_ms": 70.0, "p95": 100.0},
                                      F: {"mean_ms": 60.0, "p95": 90.0}}), {}),
    "two_sources": lambda: (NORMAL, _w(
        apps={C: {"mean_ms": 900.0, "p95": 3900.0, "pool_a": 7.5, "sessions": 8.0},
              B: {"rpm": 240.0, "pool_a": 8.0, "pool_pe": 3.0, "mean_ms": 300.0, "p95": 600.0}},
        lock_total=5.0, lock_pairs={(C, R): 5.0}, edges=[_edge(C, R, 120)], pg_lock_logs=500), {}),
    "missing_telemetry": lambda: (NORMAL, _w(collector=False),
                                  {"drop": ("spans", "edges", "pg_lock_logs", "lock_waiters", "hikari")}),
    "stale_telemetry": lambda: (NORMAL, _w(), {"stale": ("hosts", "db_conn")}),
}

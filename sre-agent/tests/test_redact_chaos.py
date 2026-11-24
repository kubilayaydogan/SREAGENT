from airlab_agent.redact import strip_fields


def test_chaos_ground_truth_fields_never_reach_agent():
    recs = [{"service": "checkin", "chaos.scenario": "S02", "chaos.experiment.id": "x", "waiter.chaos.target": "db",
             "experiment_id": "e", "blocker.lab_hold": True, "latency_ms": 12, "phase": "x-ok"}]
    out = strip_fields(recs, ["experiment_id", "lab_hold", "chaos.scenario"])
    assert out == [{"service": "checkin", "latency_ms": 12, "phase": "x-ok"}]


def test_chaos_op_values_are_masked_but_business_ops_kept():
    recs = [{"waiter.op": "lookup_booking", "blocker.op": "chaos-longtx", "blocker_op": "lab_hold_checkin",
             "waiter_op": "create_booking", "service": "chaos-controller", "content": "a waits for b"}]
    out = strip_fields(recs, [])
    assert out == [{"waiter.op": "lookup_booking", "blocker.op": "unlabeled", "blocker_op": "unlabeled",
                    "waiter_op": "create_booking", "service": "chaos-controller", "content": "a waits for b"}]

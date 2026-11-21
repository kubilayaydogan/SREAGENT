"""Kural tabanlı mekanizma tespiti ve kaynak/etkilenen uygulama atfı.

Her mekanizma yalnız TELEMETRİ kanıtına dayanır; deney/kaos etiketi (experiment_id, lab_hold) girişten silinmiştir.
Nedensellik güveni (causality_confidence) puandan ve veri kapsamından AYRI raporlanır:
  high   = birbirinden bağımsız ≥2 kaynak aynı yönü gösteriyor ve etkilenen uygulamada etki ölçülmüş
  medium = tek kaynak ya da etki ölçülemedi
  low    = yalnız zamansal çakışma / dolaylı işaret
"""
from __future__ import annotations

from typing import Any

from .scoring import latency_ratio


def _g(d: dict | None, k: str, default=None):
    return (d or {}).get(k, default)


def impacted_apps(cur: dict, base: dict, cfg: dict) -> dict[str, dict]:
    out = {}
    im = cfg["impact"]
    for app in cfg["apps"]:
        c, b = cur["apps"].get(app, {}), base["apps"].get(app, {})
        ratio, src, delta = latency_ratio(c, b, cfg)
        reasons = []
        if ratio is not None and ratio >= im["latency_ratio"] and (delta or 0) >= im["latency_min_delta_ms"]:
            reasons.append(f"gecikme ×{ratio:.1f} ({src}, +{delta:.0f} ms)")
        er, eb = c.get("err_rate"), b.get("err_rate") or 0.0
        if er is not None and er - eb >= im["error_rate_delta"]:
            reasons.append(f"5xx oranı {eb:.1%} → {er:.1%}")
        if (c.get("pool_timeouts") or 0) > 0:
            reasons.append(f"havuz zaman aşımı {c['pool_timeouts']:.0f}")
        if reasons:
            out[app] = {"reasons": reasons, "latency_ratio": ratio, "latency_source": src}
    return out


def load_increase(cur: dict, base: dict, cfg: dict) -> dict[str, float]:
    out = {}
    for app in cfg["apps"]:
        c, b = _g(cur["apps"].get(app), "rpm"), _g(base["apps"].get(app), "rpm")
        if c is not None and b and c - b >= cfg["impact"].get("min_load_rpm_delta", 0):
            out[app] = c / b
    return out


def _conf(n_sources: int, impact_seen: bool) -> str:
    if n_sources >= 2 and impact_seen:
        return "high"
    if n_sources >= 1:
        return "medium"
    return "low"


def detect(cur: dict, base: dict, cfg: dict, evidence_ids: dict[str, str | None]) -> dict[str, Any]:
    m = cfg["mechanisms"]
    imp = impacted_apps(cur, base, cfg)
    loads = load_increase(cur, base, cfg)
    db, hosts = cur["db"], cur["hosts"]
    bhosts = base["hosts"]
    mechs: list[dict] = []
    ev = lambda *ks: [evidence_ids[k] for k in ks if evidence_ids.get(k)]  # noqa: E731

    # 1) Satır kilidi
    edges = db.get("edges") or []
    edges_total = db.get("edges_total")
    waiter_avg = {a: _g(cur["apps"].get(a), "lock_waiters_avg") for a in cfg["apps"]}
    lock_srcs = []
    if (edges_total or 0) >= m["lock_min_edges"]:
        lock_srcs.append("blocking_edge logları")
    if (db.get("lock_waiters_avg") or 0) >= m["lock_min_waiters_avg"]:
        lock_srcs.append("pg lock waiters metriği")
    if (db.get("pg_lock_wait_logs") or 0) > 0:
        lock_srcs.append("PostgreSQL log_lock_waits")
    if lock_srcs:
        by_blocker: dict[str, float] = {}
        by_waiter: dict[str, float] = {}
        for e in edges:
            by_blocker[e.get("blocker_app") or "?"] = by_blocker.get(e.get("blocker_app") or "?", 0) + e["n"]
            by_waiter[e.get("waiter_app") or "?"] = by_waiter.get(e.get("waiter_app") or "?", 0) + e["n"]
        for a, v in waiter_avg.items():
            if (v or 0) >= m["lock_min_waiters_avg"]:
                by_waiter.setdefault(a, 0)
        # Kök engelleyici: kendisi Lock beklemeyen blocker (zincirde ara halkalar kaynak sayılmaz)
        root: dict[str, float] = {}
        for e in edges:
            if e.get("blocker_wait") != "Lock":
                root[e.get("blocker_app") or "?"] = root.get(e.get("blocker_app") or "?", 0) + e["n"]
        rank = root or by_blocker
        sources = sorted(rank, key=rank.get, reverse=True)
        chain = sorted(set(by_blocker) - set(sources))
        # Etkilenen: bekleyen uygulamalar; kaynak uygulama yalnız kendisinde de etki ölçüldüyse etkilenen sayılır
        affected = [a for a in sorted(by_waiter, key=lambda a: by_waiter[a], reverse=True)
                    if a not in sources or a in imp]
        blocker_states = sorted({e.get("blocker_state") for e in edges if e.get("blocker_state")})
        mechs.append({
            "id": "row_lock", "title": "Satır kilidi beklemesi",
            "source_apps": sources, "affected_apps": affected,
            "mechanism": "Kaynak uygulamanın açık transaction'ı satır kilidini tutarken etkilenen uygulamanın "
                         "UPDATE/SELECT FOR UPDATE işlemleri Lock/transactionid beklemesine düşüyor; bekleyen istekler "
                         "Hikari bağlantısını tuttuğu için havuz doluluğu ve gecikme artıyor.",
            "signals": lock_srcs, "edges_total": edges_total, "edge_pairs": edges[:10],
            "blocker_states": blocker_states, "chain_blockers": chain,
            "confidence": _conf(len(lock_srcs), any(a in imp for a in affected)),
            "evidence_ids": ev("edges", "collector", "lock_waiters", "pg_lock_logs"),
        })

    # 2) Tek uygulama havuz doygunluğu (DB genelinde baskı yokken)
    conn_ratio = db.get("conn_ratio")
    lock_affected = set(mechs[0]["affected_apps"]) if mechs else set()
    for app in cfg["apps"]:
        c = cur["apps"].get(app, {})
        pr = c.get("pool_ratio_avg", c.get("pool_ratio"))
        if pr is None:
            continue
        sat = pr >= m["pool_active_ratio"] or (c.get("pool_pending_max") or 0) > 0 or (c.get("pool_timeouts") or 0) > 0
        if sat and (conn_ratio is None or conn_ratio < m["db_conn_ratio"]):
            own_load = loads.get(app, 1.0) >= cfg["impact"]["throughput_load_ratio"]
            secondary = app in lock_affected
            srcs = ["Hikari aktif/max"] + (["istek hızı artışı"] if own_load else [])
            mechs.append({
                "id": "pool_saturation", "title": f"{app} bağlantı havuzu doygunluğu",
                "source_apps": [app] if (own_load or not secondary) else (mechs[0]["source_apps"] if mechs else []),
                "affected_apps": [app],
                "mechanism": ("Kilit beklemesinin sonucu: bekleyen istekler bağlantıları tutuyor." if secondary else
                              "Uygulamanın kendi yükü havuz kapasitesini aşıyor; DB genelinde bağlantı baskısı yok, "
                              "diğer uygulamalara yayılma beklenmez."),
                "secondary_to": "row_lock" if secondary else None,
                "pool_ratio": round(pr, 3), "pending_max": c.get("pool_pending_max"),
                "timeouts": c.get("pool_timeouts"), "rpm_ratio": round(loads.get(app, 0), 2) if app in loads else None,
                "signals": srcs, "confidence": _conf(len(srcs), app in imp),
                "evidence_ids": ev("hikari", "http"),
            })

    # 3) Global bağlantı baskısı
    if conn_ratio is not None and conn_ratio >= m["db_conn_ratio"]:
        deltas = {}
        for app in cfg["apps"]:
            cs, bs = _g(cur["apps"].get(app), "db_sessions"), _g(base["apps"].get(app), "db_sessions")
            if cs is not None:
                deltas[app] = cs - (bs or 0)
        sources = [a for a in sorted(deltas, key=deltas.get, reverse=True) if deltas[a] > 1][:2]
        mechs.append({
            "id": "global_connection_pressure", "title": "PostgreSQL genel bağlantı baskısı",
            "source_apps": sources, "affected_apps": sorted(imp),
            "mechanism": "max_connections'a yaklaşıldı; yeni bağlantı açmak isteyen tüm uygulamalar etkilenebilir "
                         "(havuzu zaten dolu olanlar daha az).",
            "conn_ratio": round(conn_ratio, 3), "session_delta_by_app": {k: round(v, 2) for k, v in deltas.items()},
            "signals": ["airlab.pg.connections.used_ratio_normal"] + (["oturum artışı (uygulama bazında)"] if sources else []),
            "confidence": _conf(1 + bool(sources), bool(imp)), "evidence_ids": ev("db_conn", "db_sessions"),
        })

    # 4) DB_VM CPU
    dbh, bdbh = hosts.get(cfg["hosts"]["db"]) or {}, bhosts.get(cfg["hosts"]["db"]) or {}
    if dbh.get("cpu") is not None and (dbh["cpu"] >= m["db_cpu_pct"] or
                                       dbh["cpu"] - (bdbh.get("cpu") or dbh["cpu"]) >= m["cpu_increase_pp"]):
        incs = {}
        for app in cfg["apps"]:
            c, b = _g(cur["apps"].get(app), "db_exec_ms_per_min"), _g(base["apps"].get(app), "db_exec_ms_per_min")
            if c is not None:
                incs[app] = c - (b or 0)
        sources = [a for a in sorted(incs, key=incs.get, reverse=True) if incs[a] > 0][:2]
        mechs.append({
            "id": "db_cpu", "title": "DB_VM CPU baskısı",
            "source_apps": sources, "affected_apps": sorted(imp),
            "mechanism": "PostgreSQL host'unda CPU doygunluğu tüm sorguların yürütme süresini uzatır; kaynak, "
                         "pg_stat_statements yürütme süresi en çok artan rol(ler)dir.",
            "db_cpu": round(dbh["cpu"], 1), "baseline_cpu": round(bdbh["cpu"], 1) if bdbh.get("cpu") is not None else None,
            "exec_ms_increase_per_min": {k: round(v, 1) for k, v in incs.items()},
            "signals": ["dt.host.cpu.usage (db-vm)"] + (["pg_stat_statements exec_time"] if sources else []),
            "confidence": _conf(1 + bool(sources), bool(imp)), "evidence_ids": ev("hosts", "statements"),
        })

    # 5) APP_VM CPU (agent'ın kendi tüketimi dahil ayrıştırılır)
    aph, baph = hosts.get(cfg["hosts"]["app"]) or {}, bhosts.get(cfg["hosts"]["app"]) or {}
    if aph.get("cpu") is not None and (aph["cpu"] >= m["app_cpu_pct"] or
                                       aph["cpu"] - (baph.get("cpu") or aph["cpu"]) >= m["cpu_increase_pp"]):
        cc, bc = cur["containers"], base["containers"]
        incs = {k: v["cpu_cores"] - (_g(bc.get(k), "cpu_cores") or 0) for k, v in cc.items()}
        top = [k for k in sorted(incs, key=incs.get, reverse=True) if incs[k] > 0.1][:2]
        mechs.append({
            "id": "app_vm_cpu", "title": "APP_VM CPU baskısı",
            "source_apps": top, "affected_apps": sorted(imp),
            "mechanism": "Tüm Java servisleri aynı host'u paylaşır; CPU doygunluğu DB ile ilgisiz biçimde her "
                         "uygulamanın gecikmesini artırır. DB_VM CPU'su normal ise DB kaynaklı değildir.",
            "app_cpu": round(aph["cpu"], 1), "baseline_cpu": round(baph["cpu"], 1) if baph.get("cpu") is not None else None,
            "container_core_increase": {k: round(v, 3) for k, v in incs.items()},
            "self_is_source": "sre-agent" in top,
            "signals": ["dt.host.cpu.usage (app-vm)"] + (["container CPU"] if top else []),
            "confidence": _conf(1 + bool(top), bool(imp)), "evidence_ids": ev("hosts", "containers"),
        })

    # 6) Bağımsız yük: yük artan uygulama var ama başka uygulamaya yayılma ve paylaşılan kaynak mekanizması yok
    shared = {x["id"] for x in mechs} & {"row_lock", "global_connection_pressure", "db_cpu", "app_vm_cpu"}
    for app, r in loads.items():
        if r >= cfg["impact"]["throughput_load_ratio"] and not shared:
            others = [a for a in imp if a != app]
            mechs.append({
                "id": "independent_load", "title": f"{app} bağımsız yük artışı",
                "source_apps": [app], "affected_apps": [app] if app in imp else [],
                "mechanism": "İstek hızı arttı; paylaşılan kaynakta (kilit, DB bağlantısı, CPU) baskı ölçülmedi. "
                             + ("Başka uygulamalarda da etki var — ilişki kanıtlanamadı." if others else
                                "Diğer uygulamalara yayılma gözlenmedi."),
                "rpm_ratio": round(r, 2), "unexplained_impact": others,
                "signals": ["Micrometer istek hızı"], "confidence": "medium" if not others else "low",
                "evidence_ids": ev("http"),
            })

    primary = [x for x in mechs if not x.get("secondary_to")]
    explained = {a for x in mechs for a in x["affected_apps"]}
    return {"impacted": imp, "load_ratio": {k: round(v, 2) for k, v in loads.items()}, "mechanisms": mechs,
            "primary": primary[0]["id"] if primary else None,
            "unexplained_impacted": sorted(set(imp) - explained)}


def alternatives(attr: dict, quality: dict) -> list[str]:
    """Rapor için alternatif açıklamalar ve dikkat notları (deterministik)."""
    ids = {m["id"] for m in attr["mechanisms"]}
    out = []
    if "row_lock" in ids and "app_vm_cpu" in ids:
        out.append("Gecikme kilit yerine APP_VM CPU doygunluğundan da kaynaklanabilir; iki mekanizma aynı anda aktif.")
    if "row_lock" in ids and "global_connection_pressure" in ids:
        out.append("Bağlantı baskısı kilit beklemesinin sonucu olabilir (bekleyen oturumlar bağlantı tutar).")
    if "db_cpu" in ids and "row_lock" not in ids:
        out.append("DB CPU artışı ağır sorgu yerine vacuum/checkpoint gibi bakım işlerinden kaynaklanıyor olabilir; "
                   "postgres.* eklenti metrikleri yok, ayrım yapılamaz.")
    if attr["unexplained_impacted"]:
        out.append(f"Şu uygulamalardaki etki hiçbir mekanizmayla açıklanamadı: {', '.join(attr['unexplained_impacted'])}"
                   " (uygulama içi hata, GC veya ağ olabilir).")
    if quality.get("spans", {}).get("status") != "OK":
        out.append("Span verisi eksik/seyrek: gecikme Micrometer ortalamasından hesaplandı, p95 değil.")
    if not ids and attr["impacted"]:
        out.append("Etki var ama ölçülen paylaşılan kaynak baskısı yok; dış bağımlılık veya uygulama içi sorun olabilir.")
    return out

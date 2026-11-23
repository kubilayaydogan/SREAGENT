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
            reasons.append(f"latency ×{ratio:.1f} ({src}, +{delta:.0f} ms)")
        er, eb = c.get("err_rate"), b.get("err_rate") or 0.0
        if er is not None and er - eb >= im["error_rate_delta"]:
            reasons.append(f"5xx rate {eb:.1%} → {er:.1%}")
        if (c.get("pool_timeouts") or 0) > 0:
            reasons.append(f"pool timeout {c['pool_timeouts']:.0f}")
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


def lock_chain(sources: list[str], edges: list[dict]) -> list[dict]:
    """Kilit bekleme zinciri: kök engelleyiciden başlayıp her seviyede bir önceki halkayı bekleyen uygulamalar.

    Kenarlar "waiter_app, blocker_app'i bekliyor" çiftleridir; uygulamanın kendi içindeki beklemeler (aynı uygulama)
    zinciri oluşturmaz. Dönen liste kök ilk olmak üzere halkalardır:
      [{"app", "level", "waits_on": [{"app", "n"}], "n"}]  (level 0 = kök, waits_on boş)
    Bir uygulama birden fazla seviyeye bağlanıyorsa en kısa yoldaki seviyesi kullanılır (BFS).
    """
    pairs: dict[tuple[str, str], float] = {}
    for e in edges:
        w, b = e.get("waiter_app"), e.get("blocker_app")
        if w and b and w != b:
            pairs[(w, b)] = pairs.get((w, b), 0) + (e.get("n") or 0)
    roots = [a for a in sources if a and a != "?"]
    if not roots or not pairs:
        return []
    level = {a: 0 for a in roots}
    out = [{"app": a, "level": 0, "waits_on": [], "n": 0} for a in roots]
    frontier = list(roots)
    while frontier:
        nxt: dict[str, list] = {}
        for (w, b), n in pairs.items():
            if b in frontier and w not in level:
                nxt.setdefault(w, []).append({"app": b, "n": n})
        if not nxt:
            break
        depth = level[frontier[0]] + 1
        for w in sorted(nxt, key=lambda a: -sum(x["n"] for x in nxt[a])):
            level[w] = depth
            out.append({"app": w, "level": depth, "waits_on": sorted(nxt[w], key=lambda x: -x["n"]),
                        "n": sum(x["n"] for x in nxt[w])})
        frontier = [w for w in nxt]
    return out


def root_sources(rank: dict[str, float], edges: list[dict], max_ratio: float = 0.5) -> list[str]:
    """Kök aday listesinden, kendisi başka uygulamaları yoğun bekleyen ara halkaları ayıklar.

    Ara halkanın tuttuğu oturum örnekleme anında Lock dışında (ör. ClientRead, CPU) yakalanabilir; bu yüzden
    "blocker_wait != Lock" tek başına kök kanıtı değildir. Bir aday, uygulamalar arası beklediği sayı (W)
    kendisinin beklettiği sayının (B) max_ratio katından azsa kök kalır. Hiçbiri kalmazsa W/B oranı en düşük olan
    seçilir. Sıralama rank (kilit dışı engelleme sayısı) azalan.
    """
    waits: dict[str, float] = {}
    waited: dict[str, float] = {}
    for e in edges:
        w, b, n = e.get("waiter_app"), e.get("blocker_app"), e.get("n") or 0
        if w and b and w != b:
            waits[w] = waits.get(w, 0) + n
            waited[b] = waited.get(b, 0) + n
    ordered = sorted(rank, key=rank.get, reverse=True)
    keep = [a for a in ordered if waits.get(a, 0) < max_ratio * waited.get(a, 0) or not waits.get(a)]
    if keep:
        return keep
    return [min(ordered, key=lambda a: waits.get(a, 0) / max(waited.get(a, 0), 1))] if ordered else []


def chain_text(chain: list[dict]) -> str:
    """'reservation → checkin → baggage' (aynı seviyedekiler '+' ile)."""
    levels: dict[int, list[str]] = {}
    for h in chain:
        levels.setdefault(h["level"], []).append(h["app"])
    return " → ".join(" + ".join(levels[k]) for k in sorted(levels))


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
        sources = root_sources(rank, edges)
        chain = sorted(set(by_blocker) - set(sources))
        # Etkilenen: bekleyen uygulamalar; kaynak uygulama yalnız kendisinde de etki ölçüldüyse etkilenen sayılır
        affected = [a for a in sorted(by_waiter, key=lambda a: by_waiter[a], reverse=True)
                    if a not in sources or a in imp]
        blocker_states = sorted({e.get("blocker_state") for e in edges if e.get("blocker_state")})
        chain_hops = lock_chain(sources, edges)
        depth = max((h["level"] for h in chain_hops), default=0)
        mechs.append({
            "id": "row_lock",
            "title": "Zincirleme row lock wait" if depth >= 2 else "Row lock wait",
            "source_apps": sources, "affected_apps": affected,
            "mechanism": "Source uygulamanın açık transaction'ı row lock'u tutarken etkilenen uygulamanın "
                         "UPDATE/SELECT FOR UPDATE işlemleri Lock/transactionid wait'e düşüyor; bekleyen request'ler "
                         "Hikari connection'ını tuttuğu için connection pool doluluğu ve latency artıyor.",
            "signals": lock_srcs, "edges_total": edges_total, "edge_pairs": edges[:10],
            "blocker_states": blocker_states, "chain_blockers": chain,
            "lock_chain": chain_hops, "chain_depth": depth, "chain_path": chain_text(chain_hops) if depth else None,
            **({"cascade": "Bekleyen her halka kendi transaction'ında tuttuğu row lock'u ve Hikari connection'ını "
                           "bırakamadığı için bir sonraki uygulamayı bekletiyor; impact source uygulamadan zincir "
                           "boyunca yayılıyor."} if depth >= 2 else {}),
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
                "id": "pool_saturation", "title": f"{app} connection pool saturation",
                "source_apps": [app] if (own_load or not secondary) else (mechs[0]["source_apps"] if mechs else []),
                "affected_apps": [app],
                "mechanism": ("Lock wait'in sonucu: bekleyen request'ler connection'ları tutuyor." if secondary else
                              "Uygulamanın kendi load'u connection pool kapasitesini aşıyor; DB genelinde connection pressure yok, "
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
            "id": "global_connection_pressure", "title": "PostgreSQL global connection pressure",
            "source_apps": sources, "affected_apps": sorted(imp),
            "mechanism": "max_connections'a yaklaşıldı; yeni connection açmak isteyen tüm uygulamalar etkilenebilir "
                         "(connection pool'u zaten dolu olanlar daha az).",
            "conn_ratio": round(conn_ratio, 3), "session_delta_by_app": {k: round(v, 2) for k, v in deltas.items()},
            "signals": ["airlab.pg.connections.used_ratio_normal"] + (["session artışı (uygulama bazında)"] if sources else []),
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
            "id": "db_cpu", "title": "DB_VM CPU pressure",
            "source_apps": sources, "affected_apps": sorted(imp),
            "mechanism": "PostgreSQL host'unda CPU saturation tüm query'lerin execution time'ını uzatır; source, "
                         "pg_stat_statements execution time'ı en çok artan role(ler)dir.",
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
            "id": "app_vm_cpu", "title": "APP_VM CPU pressure",
            "source_apps": top, "affected_apps": sorted(imp),
            "mechanism": "Tüm Java servisleri aynı host'u paylaşır; CPU saturation DB ile ilgisiz biçimde her "
                         "uygulamanın latency'sini artırır. DB_VM CPU'su normal ise DB kaynaklı değildir.",
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
                "id": "independent_load", "title": f"{app} bağımsız load artışı",
                "source_apps": [app], "affected_apps": [app] if app in imp else [],
                "mechanism": "Request rate arttı; paylaşılan resource'ta (lock, DB connection, CPU) pressure ölçülmedi. "
                             + ("Başka uygulamalarda da impact var — ilişki kanıtlanamadı." if others else
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
        out.append("Latency, lock yerine APP_VM CPU saturation'dan da kaynaklanabilir; iki mechanism aynı anda aktif.")
    if "row_lock" in ids and "global_connection_pressure" in ids:
        out.append("Connection pressure, lock wait'in sonucu olabilir (bekleyen session'lar connection tutar).")
    if "db_cpu" in ids and "row_lock" not in ids:
        out.append("DB CPU artışı ağır query yerine vacuum/checkpoint gibi maintenance job'larından kaynaklanıyor olabilir; "
                   "postgres.* extension metric'leri yok, ayrım yapılamaz.")
    if attr["unexplained_impacted"]:
        out.append(f"Şu uygulamalardaki impact hiçbir mechanism ile açıklanamadı: {', '.join(attr['unexplained_impacted'])}"
                   " (uygulama içi error, GC veya network olabilir).")
    if quality.get("spans", {}).get("status") != "OK":
        out.append("Span verisi eksik/seyrek: latency Micrometer mean'den hesaplandı, p95 değil.")
    if not ids and attr["impacted"]:
        out.append("Impact var ama ölçülen shared resource pressure yok; external dependency veya uygulama içi sorun olabilir.")
    return out

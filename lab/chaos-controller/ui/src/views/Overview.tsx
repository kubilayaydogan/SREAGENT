import { useEffect, useState } from 'react'
import { api } from '../api'
import { Chart } from '../components/Chart'
import { APP_COLOR, fmt, localTime, series } from '../lib'
import type { Overview as OV, Sample } from '../types'

export function Overview({ go }: { go: (h: string) => void }) {
  const [ov, setOv] = useState<OV | null>(null)
  const [hist, setHist] = useState<Sample[]>([])
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const [o, h] = await Promise.all([api.overview(), api.history(150)])
        if (alive) { setOv(o); setHist(h); setErr(null) }
      } catch (e) { if (alive) setErr(String(e)) }
    }
    tick()
    const t = setInterval(tick, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (!ov) return <p className="muted">{err ?? 'yükleniyor…'}</p>
  const db = ov.db
  const labels = hist.map((s) => localTime(s.at))
  const appLines = (pick: (a: Sample['apps'][string]) => number | null) =>
    ov.apps.map((c) => ({ name: c.app, color: APP_COLOR[c.app] ?? '#666', values: series(hist, (s) => {
      const a = s.apps[c.app]; return a && a.up ? pick(a) : null }) }))

  return (
    <div>
      {err && <p className="error">{err}</p>}
      {ov.active && (
        <div className="banner">
          Aktif deney: <a onClick={() => go('#/live/' + ov.active!.id)}>{ov.active.id}</a> — {ov.active.scenarioId} {ov.active.scenarioName}
          {' '}({ov.active.state}) · initiator <b>{ov.active.initiator}</b> · targets {ov.active.targets.join(', ')}
        </div>
      )}
      <section className="cards">
        {ov.apps.map((c) => {
          const s = c.sample
          return (
            <article key={c.app} className={'card ' + (s?.up ? '' : 'down')} style={{ borderTopColor: APP_COLOR[c.app] }}>
              <header>
                <h3>{c.app}</h3>
                <span className={'status ' + (s?.up ? 'ok' : 'bad')}>{s?.up ? '● UP' : '▲ DOWN'}</span>
              </header>
              <dl>
                <dt>CPU</dt><dd>{fmt(s?.cpuPct, 1, '%')}</dd>
                <dt>Heap</dt><dd>{fmt(s?.heapUsedMb, 0, 'MB')} ({fmt(c.heapPct, 0, '%')}) · live set {fmt(c.heapLivePct, 0, '%')}</dd>
                <dt>Pool</dt><dd>{s?.poolActive ?? '—'}/{s?.poolMax ?? '—'} active, {s?.poolPending ?? '—'} pending</dd>
                <dt>RPS</dt><dd>{fmt(s?.rps)}</dd>
                <dt>Error rate</dt><dd>{fmt(s?.errorPct, 1, '%')}</dd>
                <dt>p95 / p99</dt><dd>{fmt(s?.p95Ms, 0)} / {fmt(s?.p99Ms, 0)} ms</dd>
                <dt>DB conns</dt><dd>{s?.dbConnections ?? '—'} (lock wait {s?.dbLockWaits ?? '—'})</dd>
              </dl>
              <footer>
                {c.activeChaos
                  ? <span className="chip warn">⚡ {c.activeChaos.scenario}: {c.activeChaos.roles.join(', ')}</span>
                  : <span className="chip">aktif chaos yok</span>}
              </footer>
            </article>
          )
        })}
        <article className="card db">
          <header><h3>PostgreSQL (DB_VM)</h3>
            <span className={'status ' + (db?.up ? 'ok' : 'bad')}>{db?.up ? '● UP' : '▲ ölçülemiyor'}</span></header>
          {db?.up ? (
            <dl>
              <dt>Host CPU / RAM</dt><dd>{fmt(db.cpuPct, 1, '%')} / {fmt(db.memUsedPct, 0, '%')} ({db.cpus ?? '—'} vCPU, load {fmt(db.load1)})</dd>
              <dt>Connections</dt><dd>{db.connections}/{db.maxConnections} ({fmt(ov.dbConnectionsPct, 0, '%')}), active {db.active}, idle-in-tx {db.idleInTx}</dd>
              <dt>Lock waiters</dt><dd>{db.lockWaiters} · longest xact {fmt(db.longestXactSec, 1, 's')}</dd>
              <dt>TPS</dt><dd>{fmt(db.tps)} · deadlocks/min {fmt(db.deadlocksPerMin)}</dd>
              <dt>Query mean</dt><dd>{fmt(db.queryMeanMs, 2, 'ms')}</dd>
              <dt>I/O</dt><dd>read {fmt(db.readMbPerSec, 2)} / write {fmt(db.writeMbPerSec, 2)} MB/s · temp {fmt(db.tempMbPerSec, 2)} MB/s</dd>
            </dl>
          ) : <p className="error">{ov.dbError ?? 'lab_monitor bağlantısı yok'}</p>}
          <footer><span className="chip">APP_VM CPU {fmt(ov.appVm?.cpuPct, 0, '%')} · RAM {fmt(ov.appVm?.memUsedPct, 0, '%')} · disk {fmt(ov.appVm?.diskFreeGb, 1, 'GB')} boş</span></footer>
        </article>
      </section>

      <section className="grid3">
        <Chart title="p95 latency (ms)" unit="ms" labels={labels} lines={appLines((a) => a.p95Ms)} />
        <Chart title="Error rate (%)" unit="%" labels={labels} lines={appLines((a) => a.errorPct)} />
        <Chart title="RPS" labels={labels} lines={appLines((a) => a.rps)} />
        <Chart title="App CPU (%)" unit="%" labels={labels} lines={appLines((a) => a.cpuPct)} />
        <Chart title="Pool active" labels={labels} lines={appLines((a) => a.poolActive)} />
        <Chart title="DB_VM CPU (%)" unit="%" labels={labels}
          lines={[{ name: 'DB CPU', color: '#444', values: series(hist, (s) => s.db?.cpuPct) }]} />
        <Chart title="DB connections" labels={labels}
          lines={[{ name: 'connections', color: '#444', values: series(hist, (s) => s.db?.connections) }]} />
        <Chart title="Lock waiters" labels={labels}
          lines={[{ name: 'lock waiters', color: '#444', values: series(hist, (s) => s.db?.lockWaiters) }]} />
        <Chart title="DB TPS" labels={labels}
          lines={[{ name: 'TPS', color: '#444', values: series(hist, (s) => s.db?.tps) }]} />
      </section>
      <p className="muted small">Son ölçüm: {ov.sampledAt ?? '—'} · ortam: {ov.environment} · "—" = ölçülemedi (değer uydurulmaz).</p>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api } from '../api'
import { Chart } from '../components/Chart'
import { APP_COLOR, fmt, localTime, series } from '../lib'
import { roleLabel, stateLabel } from '../labels'
import type { Overview as OV, Sample } from '../types'

function Metric({ label, value, tone, title }: { label: string; value: string; tone?: string; title?: string }) {
  return <div className={'metric ' + (tone ?? '')} title={title}><span>{label}</span><b>{value}</b></div>
}

function Bar({ pct }: { pct: number | null }) {
  if (pct === null) return null
  const p = Math.max(0, Math.min(100, pct))
  return <span className={'bar ' + (p >= 90 ? 'bad' : p >= 70 ? 'warn' : '')}><i style={{ width: p + '%' }} /></span>
}

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

  if (!ov) return <p className="muted">{err ?? 'Yükleniyor…'}</p>
  const db = ov.db
  const labels = hist.map((s) => localTime(s.at))
  const appLines = (pick: (a: Sample['apps'][string]) => number | null) =>
    ov.apps.map((c) => ({ name: c.app, color: APP_COLOR[c.app] ?? '#666', values: series(hist, (s) => {
      const a = s.apps[c.app]; return a && a.up ? pick(a) : null }) }))
  const upCount = ov.apps.filter((c) => c.sample?.up).length
  const allUp = upCount === ov.apps.length && !!db?.up
  const span = hist.length > 1 ? `${labels[0]} – ${labels[labels.length - 1]}` : null

  return (
    <div>
      <div className="pagehead">
        <div>
          <h2>Genel durum</h2>
          <p className="muted">Dört uygulamanın ve PostgreSQL'in canlı ölçümleri. Değerler 3 saniyede bir yenilenir.</p>
        </div>
        <button className="primary" onClick={() => go('#/builder')}>Yeni deney kur</button>
      </div>
      {err && <p className="error">Ölçümler alınamadı: {err}</p>}

      <section className={'summary ' + (allUp ? 'ok' : 'bad')}>
        <div><span>Uygulamalar</span><b>{upCount}/{ov.apps.length} çalışıyor</b></div>
        <div><span>PostgreSQL</span><b>{db?.up ? 'çalışıyor' : 'ölçülemiyor'}</b></div>
        <div><span>Deney</span>
          {ov.active
            ? <b><a onClick={() => go('#/live/' + ov.active!.id)}>{ov.active.scenarioName}</a> · {stateLabel(ov.active.state)}</b>
            : <b>çalışan deney yok</b>}
        </div>
        <div><span>Sırada</span><b>{ov.queue.length} deney</b></div>
        <div><span>Son ölçüm</span><b>{localTime(ov.sampledAt) || '—'}</b></div>
      </section>

      {ov.active && (
        <div className="banner">
          <b>{ov.active.initiator}</b> uygulaması <b>{ov.active.scenarioName}</b> senaryosunu çalıştırıyor
          {ov.active.targets.length ? <> (hedef: {ov.active.targets.join(', ')})</> : null}. Aşağıdaki değerler bu deneyin etkisini içerir.
          {' '}<a onClick={() => go('#/live/' + ov.active!.id)}>Deneyi izle →</a>
        </div>
      )}

      <h3 className="sect">Uygulamalar</h3>
      <section className="cards">
        {ov.apps.map((c) => {
          const s = c.sample
          const errTone = s?.errorPct ? (s.errorPct >= 5 ? 'bad' : 'warn') : ''
          return (
            <article key={c.app} className={'card ' + (s?.up ? '' : 'down')} style={{ borderTopColor: APP_COLOR[c.app] }}>
              <header>
                <h3>{c.app}</h3>
                <span className={'status ' + (s?.up ? 'ok' : 'bad')}>{s?.up ? '● Çalışıyor' : '▲ Yanıt vermiyor'}</span>
              </header>
              <div className="metrics">
                <Metric label="p95 latency" value={fmt(s?.p95Ms, 0, 'ms')} title={`p99: ${fmt(s?.p99Ms, 0, 'ms')}`} />
                <Metric label="Hata oranı" value={fmt(s?.errorPct, 1, '%')} tone={errTone} />
                <Metric label="İstek hızı" value={fmt(s?.rps, 1, 'rps')} />
              </div>
              <dl>
                <dt>CPU</dt><dd>{fmt(s?.cpuPct, 1, '%')}</dd>
                <dt>Heap</dt>
                <dd title={`GC sonrası kalan canlı veri: ${fmt(c.heapLivePct, 0, '%')}`}>{fmt(c.heapPct, 0, '%')} <Bar pct={c.heapPct} /></dd>
                <dt title="Kullanılan / en fazla bağlantı">Connection Pool</dt>
                <dd>{s?.poolActive ?? '—'}/{s?.poolMax ?? '—'} <Bar pct={c.poolUsagePct} />
                  {s?.poolPending ? <small className="warntext"> {s.poolPending} istek bekliyor</small> : null}</dd>
                <dt>DB bağlantısı</dt>
                <dd>{s?.dbConnections ?? '—'}{s?.dbLockWaits ? <small className="warntext"> · {s.dbLockWaits} Lock Wait</small> : null}</dd>
              </dl>
              <footer>
                {c.activeChaos
                  ? <span className="chip warn">Deneyde: {c.activeChaos.roles.map(roleLabel).join(', ') || 'yalnız izleniyor'}</span>
                  : <span className="chip">Deneyde değil</span>}
              </footer>
            </article>
          )
        })}
      </section>

      <h3 className="sect">PostgreSQL ve sunucular</h3>
      <section className="cards">
        <article className="card db">
          <header><h3>PostgreSQL (DB_VM)</h3>
            <span className={'status ' + (db?.up ? 'ok' : 'bad')}>{db?.up ? '● Çalışıyor' : '▲ Ölçülemiyor'}</span></header>
          {db?.up ? (
            <>
              <div className="metrics">
                <Metric label="CPU" value={fmt(db.cpuPct, 1, '%')} title={`${db.cpus ?? '—'} vCPU, load ${fmt(db.load1)}`} />
                <Metric label="Bağlantı" value={`${db.connections ?? '—'}/${db.maxConnections ?? '—'}`}
                  tone={(ov.dbConnectionsPct ?? 0) >= 90 ? 'bad' : (ov.dbConnectionsPct ?? 0) >= 70 ? 'warn' : ''} />
                <Metric label="Lock Wait" value={String(db.lockWaiters ?? '—')} tone={db.lockWaiters ? 'warn' : ''}
                  title="Lock bekleyen session sayısı" />
                <Metric label="TPS" value={fmt(db.tps, 0)} title="Saniyedeki transaction sayısı" />
                <Metric label="Ortalama query" value={fmt(db.queryMeanMs, 2, 'ms')} />
              </div>
              <dl>
                <dt>Bellek</dt><dd>{fmt(db.memUsedPct, 0, '%')} <Bar pct={db.memUsedPct} /></dd>
                <dt>Session'lar</dt><dd>{db.active ?? '—'} aktif · {db.idleInTx ?? '—'} idle in transaction</dd>
                <dt>En uzun transaction</dt><dd>{fmt(db.longestXactSec, 1, 'sn')} · deadlock {fmt(db.deadlocksPerMin)} /dk</dd>
                <dt>Disk I/O</dt><dd>okuma {fmt(db.readMbPerSec, 2)} · yazma {fmt(db.writeMbPerSec, 2)} · temp {fmt(db.tempMbPerSec, 2)} MB/sn</dd>
                <dt>Yazma</dt><dd>insert {fmt(db.insertsPerSec, 0)} · update {fmt(db.updatesPerSec, 0)} satır/sn · rollback {fmt(db.rollbacksPerSec, 1)} /sn</dd>
                <dt>Bakım</dt><dd>{fmt(db.deadTuples, 0)} dead tuple · {db.parallelWorkers ?? '—'} parallel worker</dd>
              </dl>
            </>
          ) : <p className="error">{ov.dbError ?? 'Veritabanı ölçüm bağlantısı yok.'}</p>}
        </article>
        <article className="card">
          <header><h3>APP_VM</h3></header>
          <dl>
            <dt>CPU</dt><dd>{fmt(ov.appVm?.cpuPct, 0, '%')} <Bar pct={ov.appVm?.cpuPct ?? null} /></dd>
            <dt>Bellek</dt><dd>{fmt(ov.appVm?.memUsedPct, 0, '%')} <Bar pct={ov.appVm?.memUsedPct ?? null} /></dd>
            <dt>Boş disk</dt><dd>{fmt(ov.appVm?.diskFreeGb, 1, 'GB')}</dd>
          </dl>
          <footer><span className="chip">Ortam: {ov.environment}</span></footer>
        </article>
      </section>

      <h3 className="sect">Zaman içinde: uygulamalar {span && <small className="muted">{span}</small>}</h3>
      <section className="grid3">
        <Chart title="p95 latency (ms)" unit="ms" labels={labels} lines={appLines((a) => a.p95Ms)} />
        <Chart title="Hata oranı (%)" unit="%" labels={labels} lines={appLines((a) => a.errorPct)} />
        <Chart title="İstek hızı (rps)" labels={labels} lines={appLines((a) => a.rps)} />
        <Chart title="Uygulama CPU (%)" unit="%" labels={labels} lines={appLines((a) => a.cpuPct)} />
        <Chart title="Connection Pool: kullanılan bağlantı" labels={labels} lines={appLines((a) => a.poolActive)} />
      </section>
      <h3 className="sect">Zaman içinde: PostgreSQL</h3>
      <section className="grid3">
        <Chart title="DB_VM CPU (%)" unit="%" labels={labels}
          lines={[{ name: 'DB CPU', color: '#444', values: series(hist, (s) => s.db?.cpuPct) }]} />
        <Chart title="Bağlantı sayısı" labels={labels}
          lines={[{ name: 'bağlantı', color: '#444', values: series(hist, (s) => s.db?.connections) }]} />
        <Chart title="Lock bekleyen session" labels={labels}
          lines={[{ name: 'Lock Wait', color: '#444', values: series(hist, (s) => s.db?.lockWaiters) }]} />
        <Chart title="TPS" labels={labels}
          lines={[{ name: 'TPS', color: '#444', values: series(hist, (s) => s.db?.tps) }]} />
      </section>
    </div>
  )
}

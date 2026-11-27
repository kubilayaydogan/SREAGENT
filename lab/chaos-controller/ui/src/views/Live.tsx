import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Chart } from '../components/Chart'
import { APP_COLOR, agentWindows, fmt, isTerminal, localTime, rolesOf, series } from '../lib'
import type { Experiment, Sample, State } from '../types'
import { PHASES } from '../types'

const PHASE_TONE: Partial<Record<State, string>> = { BASELINING: 'base', RAMPING_UP: 'ramp', RUNNING: 'run', RECOVERING: 'rec' }

export function Live({ id, go }: { id: string; go: (h: string) => void }) {
  const [exp, setExp] = useState<Experiment | null>(null)
  const [samples, setSamples] = useState<Sample[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const since = useRef(0)

  useEffect(() => {
    let alive = true
    since.current = 0
    setSamples([])
    api.get(id, true).then((e) => {
      if (!alive) return
      setExp(e); setSamples(e.samples ?? []); since.current = (e.samples ?? []).length
    }).catch((e) => setErr(String(e)))
    const t = setInterval(async () => {
      try {
        const l = await api.live(id, since.current)
        if (!alive) return
        if (l.samples.length) { since.current += l.samples.length; setSamples((s) => [...s, ...l.samples]) }
        setExp((e) => e ? { ...e, state: l.state as State, stateReason: l.stateReason, transitions: l.transitions,
          logs: l.logs, report: l.report ?? e.report } : e)
        if (isTerminal(l.state)) {
          const full = await api.get(id, false)
          if (alive) setExp((e) => e ? { ...full, samples: e.samples } : full)
        }
      } catch (e) { if (alive) setErr(String(e)) }
    }, 2500)
    return () => { alive = false; clearInterval(t) }
  }, [id])

  if (!exp) return <p className="muted">{err ?? 'yükleniyor…'}</p>
  const terminal = isTerminal(exp.state)
  const observed = exp.report?.expectedVsObserved?.observedAffected ?? []
  const expected = exp.plan?.expectedAffected ?? []
  const labels = samples.map((s) => localTime(s.at))
  // faz bantları: örneklerin phase alanından (backend kaynaklı)
  const bands: { from: number; to: number; label: string; tone: string }[] = []
  samples.forEach((s, i) => {
    const tone = s.phase ? PHASE_TONE[s.phase] : undefined
    const last = bands[bands.length - 1]
    if (tone && last && last.label === s.phase) last.to = i
    else if (tone) bands.push({ from: i, to: i, label: s.phase as string, tone })
  })
  const apps = exp.participants?.length ? exp.participants : Object.keys(samples[samples.length - 1]?.apps ?? {})
  const appLines = (pick: (a: Sample['apps'][string]) => number | null) =>
    apps.map((a) => ({ name: a, color: APP_COLOR[a] ?? '#666',
      values: series(samples, (s) => { const x = s.apps[a]; return x && x.up ? pick(x) : null }) }))
  const lastWl = samples[samples.length - 1]?.workloads ?? []
  const ctl = lastWl.find((w) => w.controlTarget !== null)

  async function stop() {
    try { await api.stop(id); setMsg('stop isteği gönderildi (cleanup + recovery doğrulaması çalışacak)') } catch (e) { setErr(String(e)) }
  }
  async function emergency() {
    if (!window.confirm('EMERGENCY STOP: tüm deneyler ve kuyruk iptal edilir, tüm uygulamalarda cleanup çalışır. Devam?')) return
    try { const r = await api.emergency(); setMsg('emergency stop: ' + JSON.stringify(r)) } catch (e) { setErr(String(e)) }
  }
  async function rerun() {
    try { const s = await api.rerun(id); go('#/live/' + s.id) } catch (e) { setErr(String(e)) }
  }

  return (
    <div>
      {err && <p className="error">{err}</p>}
      {msg && <p className="banner">{msg}</p>}
      <header className="exp-head">
        <div>
          <h2>{exp.id}</h2>
          <p>{exp.request.scenarioId} — {exp.scenarioName} · intensity <b>{exp.intensity}</b> · seed {exp.seed}
            {exp.request.label ? ` · ${exp.request.label}` : ''}</p>
        </div>
        <div className="actions">
          {!terminal && <button onClick={stop}>Durdur</button>}
          {!terminal && <button className="danger" onClick={emergency}>EMERGENCY STOP</button>}
          {terminal && <button onClick={rerun}>Aynı parametrelerle tekrar çalıştır</button>}
          {terminal && <a className="button" href={`/api/v2/experiments/${encodeURIComponent(exp.id)}/report.md`} target="_blank" rel="noreferrer">Rapor (md)</a>}
        </div>
      </header>

      <ol className="phases">
        {PHASES.map((p) => {
          const tr = exp.transitions.find((t) => t.state === p)
          const cur = exp.state === p
          return <li key={p} className={(tr ? 'done ' : '') + (cur ? 'cur' : '')}>{p}<small>{tr ? localTime(tr.at) : ''}</small></li>
        })}
        {['FAILED', 'ABORTED', 'TIMED_OUT'].includes(exp.state) && <li className="cur bad">{exp.state}</li>}
      </ol>
      <p className="muted">{exp.stateReason}</p>
      {(() => {
        const w = agentWindows(exp.transitions)
        if (!w.baseline && !w.chaos) return null
        return <p className="agent-window">Agent penceresi (İstanbul saati):{' '}
          {w.baseline && <>baseline <code>{w.baseline[0]} – {w.baseline[1].slice(11)}</code></>}
          {w.baseline && w.chaos && ' · '}
          {w.chaos && <>chaos <code>{w.chaos[0]} – {w.chaos[1].slice(11)}</code></>}
        </p>
      })()}

      <section className="roles">
        {apps.map((a) => {
          const r = rolesOf(a, exp.initiator, exp.targets, observed)
          return (
            <span key={a} className="appchip on" style={{ borderColor: APP_COLOR[a] }}>
              <b>{a}</b> {r.length ? r.join(' · ') : 'gözlemci'}{expected.includes(a) ? ' · expected affected' : ''}
            </span>
          )
        })}
      </section>

      <section className="grid3">
        <Chart title="p95 latency (ms)" unit="ms" labels={labels} bands={bands} lines={appLines((a) => a.p95Ms)} />
        <Chart title="Error rate (%)" unit="%" labels={labels} bands={bands} lines={appLines((a) => a.errorPct)} />
        <Chart title="Pool pending" labels={labels} bands={bands} lines={appLines((a) => a.poolPending)} />
        <Chart title="App CPU (%)" unit="%" labels={labels} bands={bands} lines={appLines((a) => a.cpuPct)} />
        <Chart title="Heap (MB)" unit="MB" labels={labels} bands={bands} lines={appLines((a) => a.heapUsedMb)} />
        <Chart title="RPS" labels={labels} bands={bands} lines={appLines((a) => a.rps)} />
        <Chart title="DB_VM CPU (%)" unit="%" labels={labels} bands={bands} target={ctl?.controlTarget ?? null}
          lines={[{ name: 'DB CPU', color: '#444', values: series(samples, (s) => s.db?.cpuPct) }]} />
        <Chart title="DB connections" labels={labels} bands={bands}
          lines={[{ name: 'connections', color: '#444', values: series(samples, (s) => s.db?.connections) }]} />
        <Chart title="Lock waiters" labels={labels} bands={bands}
          lines={[{ name: 'lock waiters', color: '#444', values: series(samples, (s) => s.db?.lockWaiters) }]} />
      </section>

      <section>
        <h3>Workload (chaos eylemleri)</h3>
        <table className="table small">
          <thead><tr><th>Part</th><th>Workers</th><th>Calls/s</th><th>Error %</th><th>p95 ms</th><th>Amplification</th><th>Control value / target</th></tr></thead>
          <tbody>{lastWl.map((w) => (
            <tr key={w.part}><td>{w.part}</td><td>{w.workers}</td><td>{fmt(w.callsPerSec)}</td><td>{fmt(w.errorPct)}</td>
              <td>{fmt(w.p95Ms, 0)}</td><td>{fmt(w.amplification, 2)}</td>
              <td>{fmt(w.controlValue)} / {fmt(w.controlTarget)}</td></tr>))}
            {!lastWl.length && <tr><td colSpan={7} className="muted">aktif workload yok</td></tr>}
          </tbody>
        </table>
      </section>

      {exp.report && (
        <section>
          <h3>Sonuç: <span className={'verdict ' + exp.report.verdict}>{exp.report.verdict}</span>
            {' '}· cleanup {exp.cleanupOk ? '✔' : '✘ ' + exp.cleanupErrors.join('; ')}
            {' '}· recovery {exp.report.recovery?.recovered ? `✔ ${fmt(exp.report.recovery.recoveryTimeSec ?? null, 0, 's')}` : '✘ doğrulanamadı'}</h3>
          <div className="cols2">
            <div>
              <h4>Success criteria</h4>
              <table className="table small">
                <thead><tr><th /><th>Ölçüt</th><th>Baseline</th><th>Observed</th><th>Threshold</th><th>Sonuç</th></tr></thead>
                <tbody>{(exp.report.criteria ?? []).map((c) => (
                  <tr key={c.description}><td>{c.required ? '★' : '○'}</td><td>{c.description}</td>
                    <td>{fmt(c.baseline ?? null)}</td><td>{fmt(c.observed ?? null)}</td><td>{fmt(c.threshold ?? null)}</td>
                    <td className={'res ' + c.result}>{c.result}</td></tr>))}</tbody>
              </table>
            </div>
            <div>
              <h4>Expected vs observed impact</h4>
              {exp.report.expectedVsObserved && (
                <dl className="kv">
                  <dt>Expected affected</dt><dd>{exp.report.expectedVsObserved.expectedAffected.join(', ') || '—'}</dd>
                  <dt>Observed affected</dt><dd>{exp.report.expectedVsObserved.observedAffected.join(', ') || '—'}</dd>
                  <dt>Beklenmedik etkilenen</dt><dd>{exp.report.expectedVsObserved.unexpectedlyAffected.join(', ') || '—'}</dd>
                  <dt>Beklenip gözlenmeyen</dt><dd>{exp.report.expectedVsObserved.expectedButNotObserved.join(', ') || '—'}</dd>
                </dl>
              )}
              <h4>Recovery kontrolleri</h4>
              <ul className="small">{(exp.report.recovery?.lastChecks ?? []).map((c) => (
                <li key={c.check}>{c.pass ? '✔' : '✘'} {c.check} ({String(c.value)})</li>))}</ul>
            </div>
          </div>
        </section>
      )}

      {exp.dynatrace && (
        <section>
          <h3>Dynatrace korelasyonu</h3>
          <p className="muted small">İstekler <code>X-Chaos-*</code> başlıkları ile etiketlenir (chaos.experiment.id, chaos.scenario, chaos.initiator, chaos.target, chaos.intensity, chaos.phase). SRE Agent bu alanları görmez.</p>
          {Object.entries(exp.dynatrace).map(([k, v]) => (
            <div key={k}><b>{k}</b>{v.startsWith('http') ? <> <a href={v} target="_blank" rel="noreferrer">aç</a></> : <pre>{v}</pre>}</div>
          ))}
        </section>
      )}

      <section>
        <h3>Log</h3>
        <pre className="log">{exp.logs.slice(-80).map((l) => `${localTime(l.at)} ${l.level.padEnd(5)} ${l.message}`).join('\n')}</pre>
      </section>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Chart } from '../components/Chart'
import { APP_COLOR, agentWindows, fmt, isTerminal, localTime, phaseProgress, rolesOf, series, toMs } from '../lib'
import { PHASE_TONE, RESULT_LABEL, ROLE_LABEL, STATE_HELP, STATE_LABEL, VERDICT_HELP, humanDuration, intensityLabel, stateLabel, verdictLabel } from '../labels'
import type { Durations, Experiment, Sample, State } from '../types'
import { PHASES } from '../types'

const PHASE_LEN: Partial<Record<State, keyof Durations>> = {
  BASELINING: 'baselineSec', RAMPING_UP: 'rampSec', RUNNING: 'durationSec', RECOVERING: 'recoverySec',
}
const BAND_KEYS: [string, string][] = [['base', 'Baseline ölçümü'], ['ramp', 'Yük artırma'], ['run', 'Chaos'], ['rec', 'Toparlanma']]

export function Live({ id, go }: { id: string; go: (h: string) => void }) {
  const [exp, setExp] = useState<Experiment | null>(null)
  const [samples, setSamples] = useState<Sample[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())
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
    const clock = setInterval(() => setNow(Date.now()), 1000)
    return () => { alive = false; clearInterval(t); clearInterval(clock) }
  }, [id])

  if (!exp) return <p className="muted">{err ?? 'Yükleniyor…'}</p>
  const terminal = isTerminal(exp.state)
  const observed = exp.report?.expectedVsObserved?.observedAffected ?? []
  const expected = exp.plan?.expectedAffected ?? []
  const labels = samples.map((s) => localTime(s.at))
  // faz bantları: örneklerin phase alanından (backend kaynaklı)
  const bands: { from: number; to: number; label: string; tone: string }[] = []
  samples.forEach((s, i) => {
    const tone = s.phase ? PHASE_TONE[s.phase] : undefined
    const last = bands[bands.length - 1]
    if (tone && last && last.tone === tone) last.to = i
    else if (tone) bands.push({ from: i, to: i, label: stateLabel(s.phase), tone })
  })
  const apps = exp.participants?.length ? exp.participants : Object.keys(samples[samples.length - 1]?.apps ?? {})
  const appLines = (pick: (a: Sample['apps'][string]) => number | null) =>
    apps.map((a) => ({ name: a, color: APP_COLOR[a] ?? '#666',
      values: series(samples, (s) => { const x = s.apps[a]; return x && x.up ? pick(x) : null }) }))
  const lastWl = samples[samples.length - 1]?.workloads ?? []
  const ctl = lastWl.find((w) => w.controlTarget !== null)

  // çalışan fazın ilerlemesi: planlanan süre ve faza giriş zamanından (tarayıcı saatiyle; yaklaşık)
  const dur = exp.request.durations
  const entered = exp.transitions.filter((t) => t.state === exp.state).pop()
  const lenKey = PHASE_LEN[exp.state]
  const elapsed = entered ? Math.max(0, (now - toMs(entered.at)) / 1000) : 0
  const progress = lenKey ? phaseProgress(exp.state, elapsed, dur) : null
  const remaining = lenKey ? Math.max(0, dur[lenKey] - elapsed) : null
  const windows = agentWindows(exp.transitions)
  const rep = exp.report
  const verdict = rep?.verdict as string | undefined

  async function stop() {
    try { await api.stop(id); setMsg('Durdurma isteği gönderildi. Yük kesilir, temizlik yapılır ve toparlanma izlenir.') } catch (e) { setErr(String(e)) }
  }
  async function rerun() {
    try { const s = await api.rerun(id); go('#/live/' + s.id) } catch (e) { setErr(String(e)) }
  }

  return (
    <div>
      {err && <p className="error">Deney bilgisi alınamadı: {err}</p>}
      {msg && <p className="banner">{msg}</p>}
      <header className="exp-head">
        <div>
          <p className="crumb"><a onClick={() => go('#/history')}>← Geçmiş</a></p>
          <h2>{exp.scenarioName} <small className="muted">{exp.request.scenarioId}</small></h2>
          <p className="muted">Yoğunluk <b>{intensityLabel(exp.intensity).toLowerCase()}</b> · deney no <code>{exp.id}</code> · seed {exp.seed}
            {exp.request.label ? ` · ${exp.request.label}` : ''}</p>
        </div>
        <div className="actions">
          {!terminal && <button className="danger" onClick={stop} title="Yalnız bu deneyi durdurur; temizlik ve toparlanma izlemesi çalışır">Bu deneyi durdur</button>}
          {terminal && <button onClick={rerun}>Aynı ayarlarla tekrar çalıştır</button>}
          {terminal && <a className="button" href={`/api/v2/experiments/${encodeURIComponent(exp.id)}/report.md`} target="_blank" rel="noreferrer">Raporu indir (Markdown)</a>}
        </div>
      </header>

      <section className={'now ' + (terminal ? (exp.state === 'COMPLETED' ? 'ok' : 'bad') : 'live')}>
        <div className="now-main">
          <span className="now-label">{terminal ? 'Durum' : 'Şu anda'}</span>
          <b>{STATE_LABEL[exp.state]}</b>
          <p>{STATE_HELP[exp.state]}</p>
          {exp.stateReason && <p className="muted small">Açıklama: {exp.stateReason}</p>}
        </div>
        {progress !== null && (
          <div className="now-progress">
            <span className="bar big"><i style={{ width: progress + '%' }} /></span>
            <small>{remaining !== null && remaining > 0 ? `Bu aşamanın bitmesine yaklaşık ${humanDuration(remaining)}` : 'Aşama tamamlanmak üzere'}</small>
          </div>
        )}
      </section>

      <ol className="phases">
        {PHASES.map((p) => {
          const tr = exp.transitions.find((t) => t.state === p)
          const cur = exp.state === p
          return <li key={p} title={STATE_HELP[p]} className={(tr ? 'done ' : '') + (cur ? 'cur' : '')}>{STATE_LABEL[p]}<small>{tr ? localTime(tr.at) : ''}</small></li>
        })}
        {['FAILED', 'ABORTED', 'TIMED_OUT'].includes(exp.state) && <li className="cur bad">{STATE_LABEL[exp.state]}</li>}
      </ol>

      {rep && (
        <section className="result">
          <h3>Sonuç: <span className={'verdict ' + verdict}>{verdictLabel(verdict)}</span></h3>
          {verdict && VERDICT_HELP[verdict] && <p>{VERDICT_HELP[verdict]}</p>}
          <div className="summary">
            <div><span>Temizlik</span><b className={exp.cleanupOk ? 'oktext' : 'badtext'}>{exp.cleanupOk ? 'tamamlandı' : 'sorunlu'}</b></div>
            <div><span>Toparlanma</span>
              <b className={rep.recovery?.recovered ? 'oktext' : 'badtext'}>
                {rep.recovery?.recovered ? `sistem ${fmt(rep.recovery.recoveryTimeSec ?? null, 0, 'sn')} içinde baseline değerlerine döndü` : 'doğrulanamadı'}</b></div>
            <div><span>Etkilendiği ölçülen</span><b>{observed.join(', ') || 'yok'}</b></div>
          </div>
          {!exp.cleanupOk && <p className="error">Temizlik hataları: {exp.cleanupErrors.join('; ')}</p>}
          <div className="cols2">
            <div>
              <h4>Başarı ölçütleri</h4>
              <table className="table small">
                <thead><tr><th>Ölçüt</th><th title="Chaos öncesi ölçülen değer">Baseline</th><th title="Chaos sırasında ölçülen değer">Ölçülen</th>
                  <th title="Ölçütün sağlanması için gereken sınır">Sınır</th><th>Sonuç</th></tr></thead>
                <tbody>{(rep.criteria ?? []).map((c) => (
                  <tr key={c.description}><td><span className={'tag ' + (c.required ? 'req' : '')}>{c.required ? 'zorunlu' : 'bilgi'}</span> {c.description}</td>
                    <td>{fmt(c.baseline ?? null)}</td><td>{fmt(c.observed ?? null)}</td><td>{fmt(c.threshold ?? null)}</td>
                    <td className={'res ' + c.result}>{RESULT_LABEL[c.result ?? ''] ?? c.result ?? '—'}</td></tr>))}</tbody>
              </table>
              <p className="help">Sonuç yalnız “zorunlu” ölçütlere göre verilir.</p>
            </div>
            <div>
              <h4>Beklenen ve ölçülen etki</h4>
              {rep.expectedVsObserved && (
                <dl className="kv">
                  <dt>Etkilenmesi beklenen</dt><dd>{rep.expectedVsObserved.expectedAffected.join(', ') || '—'}</dd>
                  <dt>Etkilendiği ölçülen</dt><dd>{rep.expectedVsObserved.observedAffected.join(', ') || '—'}</dd>
                  <dt>Beklenmediği halde etkilenen</dt><dd>{rep.expectedVsObserved.unexpectedlyAffected.join(', ') || '—'}</dd>
                  <dt>Beklendiği halde etkilenmeyen</dt><dd>{rep.expectedVsObserved.expectedButNotObserved.join(', ') || '—'}</dd>
                </dl>
              )}
              <h4>Toparlanma kontrolleri</h4>
              <ul className="checks">{(rep.recovery?.lastChecks ?? []).map((c) => (
                <li key={c.check} className={c.pass ? 'oktext' : 'badtext'}>{c.pass ? '✔' : '✘'} {c.check} <span className="muted">({String(c.value)})</span></li>))}</ul>
            </div>
          </div>
        </section>
      )}

      <h3 className="sect">Uygulamaların rolü</h3>
      <section className="roles">
        {apps.map((a) => {
          const r = rolesOf(a, exp.initiator, exp.targets, observed).map((x) => ROLE_LABEL[x] ?? x)
          if (expected.includes(a)) r.push('etkilenmesi bekleniyor')
          return (
            <span key={a} className="appchip on" style={{ borderColor: APP_COLOR[a] }}>
              <b>{a}</b> {r.length ? r.join(' · ') : 'yalnız izleniyor'}
            </span>
          )
        })}
      </section>

      {(windows.baseline || windows.chaos) && (
        <section className="agent-window">
          <b>SRE Agent'a sormak için zaman aralıkları (TSİ)</b>
          <span>Karşılaştırma dönemi (baseline): {windows.baseline ? <code>{windows.baseline[0]} – {windows.baseline[1].slice(11)}</code> : '—'}</span>
          <span>Chaos dönemi: {windows.chaos ? <code>{windows.chaos[0]} – {windows.chaos[1].slice(11)}</code> : 'henüz bitmedi'}</span>
        </section>
      )}

      <h3 className="sect">Ölçümler
        <span className="bandkey">{BAND_KEYS.map(([t, l]) => <span key={t}><i className={'band ' + t} />{l}</span>)}</span>
      </h3>
      <section className="grid3">
        <Chart title="p95 latency (ms)" unit="ms" labels={labels} bands={bands} lines={appLines((a) => a.p95Ms)} />
        <Chart title="Hata oranı (%)" unit="%" labels={labels} bands={bands} lines={appLines((a) => a.errorPct)} />
        <Chart title="Connection Pool: bekleyen istek" labels={labels} bands={bands} lines={appLines((a) => a.poolPending)} />
        <Chart title="Uygulama CPU (%)" unit="%" labels={labels} bands={bands} lines={appLines((a) => a.cpuPct)} />
        <Chart title="Heap (MB)" unit="MB" labels={labels} bands={bands} lines={appLines((a) => a.heapUsedMb)} />
        <Chart title="İstek hızı (rps)" labels={labels} bands={bands} lines={appLines((a) => a.rps)} />
        <Chart title="DB_VM CPU (%)" unit="%" labels={labels} bands={bands} target={ctl?.controlTarget ?? null}
          lines={[{ name: 'DB CPU', color: '#444', values: series(samples, (s) => s.db?.cpuPct) }]} />
        <Chart title="DB bağlantı sayısı" labels={labels} bands={bands}
          lines={[{ name: 'bağlantı', color: '#444', values: series(samples, (s) => s.db?.connections) }]} />
        <Chart title="Lock bekleyen session" labels={labels} bands={bands}
          lines={[{ name: 'Lock Wait', color: '#444', values: series(samples, (s) => s.db?.lockWaiters) }]} />
      </section>

      <h3 className="sect">Uygulanan yük</h3>
      <p className="help">Chaos Controller'ın şu anda ürettiği yük. Deney bittiğinde bu tablo boşalır.</p>
      <table className="table small">
        <thead><tr><th>Yük parçası</th><th title="Eşzamanlı çalışan worker sayısı">Worker</th><th>Çağrı /sn</th><th>Hata %</th><th>p95 (ms)</th>
          <th title="Bir çağrının retry'lerle kaç isteğe dönüştüğü">Çoğalma katsayısı</th>
          <th title="Yükün ayarlandığı ölçümün şu anki değeri ve hedefi">Ölçülen / hedef</th></tr></thead>
        <tbody>{lastWl.map((w) => (
          <tr key={w.part}><td>{w.part}</td><td>{w.workers}</td><td>{fmt(w.callsPerSec)}</td><td>{fmt(w.errorPct)}</td>
            <td>{fmt(w.p95Ms, 0)}</td><td>{fmt(w.amplification, 2)}</td>
            <td>{w.controlTarget === null ? 'sabit seviye' : `${fmt(w.controlValue)} / ${fmt(w.controlTarget)}`}</td></tr>))}
          {!lastWl.length && <tr><td colSpan={7} className="muted">Şu anda uygulanan yük yok.</td></tr>}
        </tbody>
      </table>

      {exp.dynatrace && (
        <details className="adv">
          <summary>Dynatrace'te bu deneyi bulmak</summary>
          <p className="help">Deney istekleri <code>X-Chaos-*</code> başlıklarıyla etiketlenir. SRE Agent bu etiketleri görmez; böylece nedeni kendi bulmak zorundadır.</p>
          {Object.entries(exp.dynatrace).map(([k, v]) => (
            <div key={k}><b>{k}</b>{v.startsWith('http') ? <> <a href={v} target="_blank" rel="noreferrer">aç</a></> : <pre>{v}</pre>}</div>
          ))}
        </details>
      )}

      <details className="adv">
        <summary>Olay kaydı <small className="muted">{exp.logs.length} satır</small></summary>
        <pre className="log">{exp.logs.slice(-80).map((l) => `${localTime(l.at)} ${l.level.padEnd(5)} ${l.message}`).join('\n')}</pre>
      </details>
    </div>
  )
}

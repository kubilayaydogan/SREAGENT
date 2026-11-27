import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { APPS, APP_COLOR, BuilderForm, DEFAULT_DURATIONS, DEFAULT_TRAFFIC, buildRequest, newClientRequestId } from '../lib'
import type { Custom, Intensity, Scenario, TargetMode } from '../types'

const MODES: { v: TargetMode; label: string; help: string }[] = [
  { v: 'SINGLE', label: 'Tek uygulama', help: 'yalnız initiator (veya seçilen tek hedef)' },
  { v: 'MULTI', label: 'Çoklu', help: 'seçilen uygulamalar' },
  { v: 'ALL', label: 'Tümü', help: 'dört uygulama' },
  { v: 'SHARED_DB', label: 'Paylaşılan PostgreSQL', help: 'DB üzerinden tüm uygulamalar' },
  { v: 'RANDOM', label: 'Rastgele', help: 'seed\'e bağlı alt küme' },
  { v: 'AUTO', label: 'Otomatik (bağımlılık)', help: 'senaryonun doğal hedefleri' },
]
const INTENSITIES: Intensity[] = ['LIGHT', 'MEDIUM', 'HIGH', 'CRITICAL', 'CUSTOM']

function num(v: string): number | null {
  return v.trim() === '' ? null : Number(v)
}

export function Builder({ preset, go }: { preset?: string; go: (h: string) => void }) {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [f, setF] = useState<BuilderForm>({
    scenarioId: preset ?? 'S02', initiator: 'checkin', targetMode: 'AUTO', targets: [], intensity: 'MEDIUM',
    custom: {}, durations: DEFAULT_DURATIONS, traffic: DEFAULT_TRAFFIC, seed: '', label: '',
  })
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'manual' | 'random'>('manual')
  const [rp, setRp] = useState({ seed: '', count: 3, parallel: false, cooldownSec: 60, maxIntensity: 'MEDIUM' as Intensity,
    allowedScenarios: [] as string[], excludedScenarios: [] as string[], allowedApps: [] as string[], excludedApps: [] as string[] })

  useEffect(() => { api.scenarios().then(setScenarios).catch((e) => setErr(String(e))) }, [])
  useEffect(() => { if (preset) setF((x) => ({ ...x, scenarioId: preset })) }, [preset])

  const sc = useMemo(() => scenarios.find((s) => s.id === f.scenarioId), [scenarios, f.scenarioId])
  const set = <K extends keyof BuilderForm>(k: K, v: BuilderForm[K]) => { setF((x) => ({ ...x, [k]: v })); setPreview(null) }
  const setCustom = (k: keyof Custom, v: number | null) => set('custom', { ...f.custom, [k]: v })
  const setBudget = (k: string, v: number | null) => set('custom', { ...f.custom, budget: { ...(f.custom.budget ?? {}), [k]: v } })

  async function doPreview() {
    setErr(null)
    try { setPreview(await api.preview(buildRequest(f, 'preview'))) } catch (e) { setErr(String(e)); setPreview(null) }
  }
  async function start() {
    setBusy(true); setErr(null)
    try {
      const s = await api.submit(buildRequest(f, newClientRequestId()))
      go('#/live/' + s.id)
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  async function startRandom() {
    setBusy(true); setErr(null)
    try {
      const r = await api.randomPlan({
        seed: rp.seed ? Number(rp.seed) : null, allowedScenarios: rp.allowedScenarios, excludedScenarios: rp.excludedScenarios,
        allowedApps: rp.allowedApps, excludedApps: rp.excludedApps, maxIntensity: rp.maxIntensity, count: rp.count,
        parallel: rp.parallel, cooldownSec: rp.cooldownSec, durations: f.durations, traffic: f.traffic,
      })
      setPreview({ randomPlan: r })
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  const toggle = (list: string[], v: string) => list.includes(v) ? list.filter((x) => x !== v) : [...list, v]

  return (
    <div className="builder">
      <div className="tabs">
        <button className={tab === 'manual' ? 'on' : ''} onClick={() => setTab('manual')}>Manuel deney</button>
        <button className={tab === 'random' ? 'on' : ''} onClick={() => setTab('random')}>Rastgele mod</button>
      </div>
      {err && <p className="error">{err}</p>}

      <div className="cols2">
        <div>
          {tab === 'manual' ? (
            <>
              <fieldset><legend>1. Senaryo</legend>
                <select value={f.scenarioId} onChange={(e) => set('scenarioId', e.target.value)}>
                  {scenarios.map((s) => <option key={s.id} value={s.id}>{s.id} — {s.name}</option>)}
                </select>
                {sc && <p className="muted small">{sc.description}</p>}
              </fieldset>

              <fieldset><legend>2. Initiator (yükü başlatan uygulama)</legend>
                <div className="apps">
                  {APPS.map((a) => (
                    <label key={a} className={'appchip ' + (f.initiator === a ? 'on' : '')} style={{ borderColor: APP_COLOR[a] }}>
                      <input type="radio" name="init" checked={f.initiator === a} onChange={() => set('initiator', a)} />{a}
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset><legend>3. Hedef seçimi</legend>
                <div className="modes">
                  {MODES.map((m) => (
                    <label key={m.v} title={m.help} className={f.targetMode === m.v ? 'on' : ''}>
                      <input type="radio" name="mode" checked={f.targetMode === m.v} onChange={() => set('targetMode', m.v)} />{m.label}
                    </label>
                  ))}
                </div>
                {(f.targetMode === 'MULTI' || f.targetMode === 'SINGLE') && (
                  <div className="apps">
                    {APPS.map((a) => (
                      <label key={a} className={'appchip ' + (f.targets.includes(a) ? 'on' : '')} style={{ borderColor: APP_COLOR[a] }}>
                        <input type="checkbox" checked={f.targets.includes(a)}
                          onChange={() => set('targets', f.targetMode === 'SINGLE' ? [a] : toggle(f.targets, a))} />{a}
                      </label>
                    ))}
                  </div>
                )}
                {sc && <p className="muted small">Direct target: <b>{sc.directTarget}</b> · min. {sc.minApps} uygulama</p>}
              </fieldset>

              <fieldset><legend>4. Yoğunluk</legend>
                <div className="modes">
                  {INTENSITIES.map((i) => (
                    <label key={i} className={'int-' + i.toLowerCase() + (f.intensity === i ? ' on' : '')}>
                      <input type="radio" name="int" checked={f.intensity === i} onChange={() => set('intensity', i)} />{i}
                    </label>
                  ))}
                </div>
                {f.intensity === 'CUSTOM' && (
                  <div className="form-grid">
                    {([
                      ['workers', 'Workers (eşzamanlı)'], ['rps', 'RPS'], ['dbConnections', 'DB connections'],
                      ['txDurationMs', 'Transaction süresi (ms)'], ['queryFrequency', 'Query frekansı (/s/worker)'],
                      ['targetValue', 'Kontrol hedefi (ör. DB CPU %)'], ['errorPct', 'Error olasılığı (%)'],
                      ['networkLatencyMs', 'Network latency (ms)'], ['packetLossPct', 'Packet loss (%)'],
                      ['maxAffectedServices', 'Maks. etkilenen servis'],
                    ] as [keyof Custom, string][]).map(([k, l]) => (
                      <label key={k}>{l}<input type="number" value={(f.custom[k] as number | null | undefined) ?? ''}
                        onChange={(e) => setCustom(k, num(e.target.value))} /></label>
                    ))}
                    {([['maxDbCpuPct', 'Budget: DB CPU %'], ['maxDbConnectionsPct', 'Budget: DB conn %'],
                      ['maxAppHeapPct', 'Budget: app heap %'], ['maxDbMemPct', 'Budget: DB mem %']] as [string, string][]).map(([k, l]) => (
                      <label key={k}>{l}<input type="number"
                        value={((f.custom.budget ?? {}) as Record<string, number | null>)[k] ?? ''}
                        onChange={(e) => setBudget(k, num(e.target.value))} /></label>
                    ))}
                  </div>
                )}
              </fieldset>
            </>
          ) : (
            <fieldset><legend>Rastgele mod (aynı güvenlik kuralları)</legend>
              <div className="form-grid">
                <label>Seed<input value={rp.seed} placeholder="boş = rastgele" onChange={(e) => setRp({ ...rp, seed: e.target.value })} /></label>
                <label>Senaryo sayısı<input type="number" min={1} max={10} value={rp.count} onChange={(e) => setRp({ ...rp, count: Number(e.target.value) })} /></label>
                <label>Maks. yoğunluk<select value={rp.maxIntensity} onChange={(e) => setRp({ ...rp, maxIntensity: e.target.value as Intensity })}>
                  {INTENSITIES.slice(0, 4).map((i) => <option key={i}>{i}</option>)}</select></label>
                <label>Cooldown (s)<input type="number" value={rp.cooldownSec} onChange={(e) => setRp({ ...rp, cooldownSec: Number(e.target.value) })} /></label>
                <label className="inline"><input type="checkbox" checked={rp.parallel} onChange={(e) => setRp({ ...rp, parallel: e.target.checked })} />Paralel (tek composite deney)</label>
              </div>
              <h4>İzinli senaryolar (boş = tümü)</h4>
              <div className="apps wrap">{scenarios.map((s) => (
                <label key={s.id} className={'appchip ' + (rp.allowedScenarios.includes(s.id) ? 'on' : '')}>
                  <input type="checkbox" checked={rp.allowedScenarios.includes(s.id)}
                    onChange={() => setRp({ ...rp, allowedScenarios: toggle(rp.allowedScenarios, s.id) })} />{s.id}</label>))}
              </div>
              <h4>Hariç senaryolar</h4>
              <div className="apps wrap">{scenarios.map((s) => (
                <label key={s.id} className={'appchip ' + (rp.excludedScenarios.includes(s.id) ? 'on bad' : '')}>
                  <input type="checkbox" checked={rp.excludedScenarios.includes(s.id)}
                    onChange={() => setRp({ ...rp, excludedScenarios: toggle(rp.excludedScenarios, s.id) })} />{s.id}</label>))}
              </div>
              <h4>Uygulamalar: izinli / hariç</h4>
              <div className="apps">{APPS.map((a) => (
                <span key={a} className="pair">
                  <label className={'appchip ' + (rp.allowedApps.includes(a) ? 'on' : '')}><input type="checkbox" checked={rp.allowedApps.includes(a)}
                    onChange={() => setRp({ ...rp, allowedApps: toggle(rp.allowedApps, a) })} />{a}</label>
                  <label className={'appchip ' + (rp.excludedApps.includes(a) ? 'on bad' : '')}><input type="checkbox" checked={rp.excludedApps.includes(a)}
                    onChange={() => setRp({ ...rp, excludedApps: toggle(rp.excludedApps, a) })} />hariç</label>
                </span>))}
              </div>
            </fieldset>
          )}

          <fieldset><legend>Süreler (s)</legend>
            <div className="form-grid">
              {(['baselineSec', 'rampSec', 'durationSec', 'recoverySec', 'maxDurationSec'] as const).map((k) => (
                <label key={k}>{k}<input type="number" value={f.durations[k]}
                  onChange={(e) => set('durations', { ...f.durations, [k]: Number(e.target.value) })} /></label>
              ))}
            </div>
          </fieldset>

          <fieldset><legend>Arka plan trafiği (kullanıcı simülasyonu)</legend>
            <div className="form-grid">
              <label className="inline"><input type="checkbox" checked={f.traffic.enabled}
                onChange={(e) => set('traffic', { ...f.traffic, enabled: e.target.checked })} />etkin</label>
              {([['rpsPerApp', 'RPS / uygulama'], ['users', 'Kullanıcı'], ['readWriteRatio', 'Read oranı (0–1)'],
                ['timeoutMs', 'Timeout (ms)'], ['retries', 'Retry'], ['retryBackoffMs', 'Retry backoff (ms)'],
                ['thinkTimeMs', 'Think time (ms)'], ['maxTotalRps', 'Toplam RPS limiti']] as [keyof typeof f.traffic, string][]).map(([k, l]) => (
                <label key={k}>{l}<input type="number" step="any" value={f.traffic[k] as number}
                  onChange={(e) => set('traffic', { ...f.traffic, [k]: Number(e.target.value) })} /></label>
              ))}
            </div>
            <div className="apps">{APPS.map((a) => (
              <label key={a} className={'appchip ' + (f.traffic.apps.includes(a) ? 'on' : '')}>
                <input type="checkbox" checked={f.traffic.apps.includes(a)}
                  onChange={() => set('traffic', { ...f.traffic, apps: toggle(f.traffic.apps, a) })} />{a}</label>))}
              <span className="muted small">seçim yok = tüm uygulamalar</span>
            </div>
          </fieldset>

          {tab === 'manual' && (
            <fieldset><legend>Tekrarlanabilirlik</legend>
              <div className="form-grid">
                <label>Seed<input value={f.seed} placeholder="boş = rastgele" onChange={(e) => set('seed', e.target.value)} /></label>
                <label>Etiket<input value={f.label} onChange={(e) => set('label', e.target.value)} /></label>
              </div>
            </fieldset>
          )}

          <div className="actions">
            {tab === 'manual' ? (
              <>
                <button onClick={doPreview}>Önizle (dry-run)</button>
                <button className="primary" disabled={busy} onClick={start}>Deneyi başlat</button>
              </>
            ) : <button className="primary" disabled={busy} onClick={startRandom}>Rastgele planı kuyruğa al</button>}
          </div>
        </div>

        <aside>
          <h3>Plan önizleme</h3>
          {!preview && <p className="muted">Önizle'ye basın: backend hedefleri çözer, kalibre parametreleri, kontrol hedefini ve success criteria'yı gösterir. Deney başlamaz.</p>}
          {preview && 'randomPlan' in preview ? (
            <pre>{JSON.stringify(preview.randomPlan, null, 1)}</pre>
          ) : preview && (
            <div className="preview">
              <p><b>Initiator:</b> {String(preview.initiator)} · <b>Direct targets:</b> {(preview.targets as string[]).join(', ')}</p>
              <p><b>Katılımcılar:</b> {(preview.participants as string[]).join(', ')}</p>
              <p><b>Beklenen etkilenen:</b> {(preview.expectedAffected as string[]).join(', ') || '— (etki DB/workload seviyesinde)'}</p>
              <p><b>Seed:</b> {String(preview.seed)}</p>
              <h4>Eylemler</h4>
              <ul>{(preview.actions as Record<string, unknown>[]).map((a) => (
                <li key={String(a.part)}><code>{String(a.kind)}</code> {String(a.part)} → {String(a.app)}
                  {a.path ? <> <code>{String(a.path)}</code> {JSON.stringify(a.params)}</> : null}
                  {a.kind === 'LOOP' ? ` workers ${a.minWorkers}–${a.maxWorkers}${a.controlled ? ' (adaptive)' : ''}` : ''}
                  {a.kind === 'NETEM' ? ` ${a.netemTarget}: delay ${a.delayMs} ms, loss ${a.lossPct}%` : ''}
                  {a.kind === 'TRAFFIC' ? ` ${a.trafficRps} rps, timeout ${a.trafficTimeoutMs} ms, retry ${a.trafficRetries}${a.hotRows ? ', hot rows' : ''}` : ''}
                </li>))}</ul>
              {preview.control ? <p><b>Adaptive control:</b> {JSON.stringify(preview.control)}</p> : <p><b>Kontrol:</b> sabit seviye</p>}
              <h4>Success criteria</h4>
              <ul>{(preview.criteria as Record<string, unknown>[]).map((c) => (
                <li key={String(c.description)}>{c.required ? '★' : '○'} {String(c.description)} — {String(c.metric)} {String(c.expected)}</li>))}</ul>
              <h4>Parametreler</h4>
              <pre>{JSON.stringify(preview.parameters, null, 1)}</pre>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

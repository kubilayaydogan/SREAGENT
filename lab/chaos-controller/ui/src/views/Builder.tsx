import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { APPS, APP_COLOR, BuilderForm, DEFAULT_DURATIONS, DEFAULT_TRAFFIC, buildRequest, newClientRequestId } from '../lib'
import { DURATION_FIELDS, DURATION_RANGE, INTENSITY_HELP, INTENSITY_LABEL, LOAD_LABEL, categoryLabel, humanDuration, intensityLabel, metricLabel, paramLabel, showValue } from '../labels'
import type { Custom, Intensity, Scenario } from '../types'

const INTENSITIES: Intensity[] = ['LIGHT', 'MEDIUM', 'HIGH', 'CRITICAL', 'CUSTOM']

const CUSTOM_FIELDS: [keyof Custom, string][] = [
  ['workers', 'Eşzamanlı worker'], ['dbConnections', 'DB bağlantısı / session'],
  ['txDurationMs', 'Transaction süresi (ms)'], ['queryFrequency', 'Query sıklığı (worker başına /sn)'],
  ['targetValue', 'Hedef seviye (ör. DB CPU %)'], ['errorPct', 'Hata olasılığı (%)'],
  ['maxAffectedServices', 'En fazla katılan uygulama'],
]
const BUDGET_FIELDS: [string, string][] = [
  ['maxDbCpuPct', 'DB CPU en fazla (%)'], ['maxDbConnectionsPct', 'DB bağlantısı en fazla (%)'],
  ['maxAppHeapPct', 'Uygulama heap en fazla (%)'], ['maxDbMemPct', 'DB bellek en fazla (%)'],
]
const TRAFFIC_FIELDS: [string, string, string][] = [
  ['rpsPerApp', 'Uygulama başına istek hızı (rps)', ''], ['users', 'Sanal kullanıcı', ''],
  ['readWriteRatio', 'Okuma oranı (0–1)', '0.8 = isteklerin %80\'i okuma'], ['timeoutMs', 'Timeout (ms)', ''],
  ['retries', 'Retry sayısı', ''], ['retryBackoffMs', 'Retry bekleme (ms)', ''],
  ['thinkTimeMs', 'İstekler arası bekleme (ms)', ''], ['maxTotalRps', 'Toplam istek hızı sınırı (rps)', ''],
]

function num(v: string): number | null {
  return v.trim() === '' ? null : Number(v)
}

type Action = Record<string, unknown>

function actionText(a: Action): string {
  const app = String(a.app ?? '')
  switch (a.kind) {
    case 'LOOP': {
      const params = (a.params ?? {}) as Record<string, string>
      let load = LOAD_LABEL[String(a.path)] ?? 'tekrarlayan yük'
      if (a.path === '/lab/chaos/table-lock') {
        load = params.mode === 'hold' ? 'tabloyu kilitleyip tutar (ACCESS EXCLUSIVE)' : 'kilitli tabloyu okumaya çalışır'
      }
      const workers = a.minWorkers === a.maxWorkers ? `${a.maxWorkers} worker` : `${a.minWorkers}–${a.maxWorkers} worker`
      return `${app}: ${load} · ${workers}` + (a.controlled ? ' (sayı kendiliğinden ayarlanır)' : '')
    }
    case 'NETEM':
      return `${app} → ${a.netemTarget} arasına ${a.delayMs} ms latency, %${a.lossPct} packet loss`
    case 'TRAFFIC':
      return `${app} için arka plan trafiği: ${a.trafficRps} rps, timeout ${a.trafficTimeoutMs} ms, ${a.trafficRetries} retry` + (a.hotRows ? ', aynı satırlara yoğunlaşır' : '')
    default:
      return `${app}: ${String(a.kind)}`
  }
}

/** Senaryonun önerdiği uygulama kümesi; ilk eleman yükü ilk başlatandır. */
function recommended(sc: Scenario | undefined, first: string): string[] {
  const others = APPS.filter((a) => a !== first)
  if (!sc) return [first]
  if (sc.defaultAllTargets) return [first, ...others]
  return [first, ...others.slice(0, Math.max(0, sc.minApps - 1))]
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
  /** Yükü başlatan uygulamalar (ilk eleman = ilk başlatan). Kullanıcı dokunmadıysa senaryo değişince öneri uygulanır. */
  const [apps, setApps] = useState<string[]>(['checkin'])
  const [randomApps, setRandomApps] = useState(false)
  const [touched, setTouched] = useState(false)
  const [rp, setRp] = useState({ seed: '', count: 3, parallel: false, cooldownSec: 60, maxIntensity: 'MEDIUM' as Intensity,
    allowedScenarios: [] as string[], excludedScenarios: [] as string[], allowedApps: [] as string[], excludedApps: [] as string[] })

  useEffect(() => { api.scenarios().then(setScenarios).catch((e) => setErr(String(e))) }, [])
  useEffect(() => { if (preset) setF((x) => ({ ...x, scenarioId: preset })) }, [preset])

  const sc = useMemo(() => scenarios.find((s) => s.id === f.scenarioId), [scenarios, f.scenarioId])
  const set = <K extends keyof BuilderForm>(k: K, v: BuilderForm[K]) => { setF((x) => ({ ...x, [k]: v })); setPreview(null) }
  const setCustom = (k: keyof Custom, v: number | null) => set('custom', { ...f.custom, [k]: v })
  const setBudget = (k: string, v: number | null) => set('custom', { ...f.custom, budget: { ...(f.custom.budget ?? {}), [k]: v } })
  const toggle = (list: string[], v: string) => list.includes(v) ? list.filter((x) => x !== v) : [...list, v]
  const pickApps = (next: string[]) => { setApps(next); setRandomApps(false); setTouched(true); setPreview(null) }

  useEffect(() => {
    if (sc && !touched) setApps((cur) => recommended(sc, cur[0] ?? 'checkin'))
  }, [sc, touched])
  // seçim → API alanları: 4 uygulama = ALL, aksi halde MULTI (ilk başlatan dahil); rastgele = RANDOM
  useEffect(() => {
    setF((x) => randomApps
      ? { ...x, initiator: '', targetMode: 'RANDOM', targets: [] }
      : { ...x, initiator: apps[0] ?? '', targetMode: apps.length === APPS.length ? 'ALL' : 'MULTI', targets: apps })
  }, [apps, randomApps])

  const d = f.durations
  const total = d.baselineSec + d.rampSec + d.durationSec + d.recoverySec
  const problems: string[] = []
  if (!randomApps && apps.length === 0) problems.push('Yükü başlatacak en az bir uygulama seçin.')
  if (sc && !randomApps && apps.length > 0 && apps.length < sc.minApps) problems.push(`Bu senaryo en az ${sc.minApps} uygulama ister.`)
  for (const x of DURATION_FIELDS) {
    const r = DURATION_RANGE[x.k]
    if (r && (!Number.isFinite(d[x.k]) || d[x.k] < r[0] || d[x.k] > r[1])) problems.push(`${x.label} ${r[0]}–${r[1]} sn aralığında olmalı.`)
  }
  if (d.maxDurationSec > 0 && total > d.maxDurationSec) problems.push('Toplam süre, “En uzun süre” sınırını aşıyor.')
  const severe = f.intensity === 'HIGH' || f.intensity === 'CRITICAL'
  const cats = [...new Set(scenarios.map((s) => s.category))]

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

  const randomPlan = preview && 'randomPlan' in preview
    ? preview.randomPlan as { planId: string; seed: string; experiments: Record<string, unknown>[] } : null

  return (
    <div className="builder">
      <div className="pagehead">
        <div>
          <h2>Yeni deney</h2>
          <p className="muted">Adımları sırayla doldurun. Sağdaki özet seçimlerinizi düz cümleyle gösterir; “Planı kontrol et” deneyi başlatmadan ne olacağını listeler.</p>
        </div>
        <div className="tabs">
          <button className={tab === 'manual' ? 'on' : ''} onClick={() => { setTab('manual'); setPreview(null) }}>Tek senaryo</button>
          <button className={tab === 'random' ? 'on' : ''} onClick={() => { setTab('random'); setPreview(null) }}
            title="Birkaç senaryoyu rastgele seçip sıraya alır">Rastgele plan</button>
        </div>
      </div>
      {err && <p className="error">İstek kabul edilmedi: {err}</p>}

      <div className="cols2">
        <div>
          {tab === 'manual' ? (
            <>
              <fieldset><legend><span className="step">1</span> Veritabanına hangi yük uygulansın?</legend>
                <select className="wide" value={f.scenarioId} onChange={(e) => set('scenarioId', e.target.value)}>
                  {cats.map((c) => (
                    <optgroup key={c} label={categoryLabel(c)}>
                      {scenarios.filter((s) => s.category === c).map((s) => <option key={s.id} value={s.id}>{s.id} — {s.name}</option>)}
                    </optgroup>
                  ))}
                </select>
                {sc && <p className="help"><span className="chip">{categoryLabel(sc.category)}</span> {sc.description}</p>}
                {sc && sc.expectedImpact.length > 0 && (
                  <details className="impact">
                    <summary>Beklenen etki</summary>
                    <ul>{sc.expectedImpact.map((x) => <li key={x}>{x}</li>)}</ul>
                  </details>
                )}
              </fieldset>

              <fieldset><legend><span className="step">2</span> Yükü hangi uygulamalar başlatsın?</legend>
                <div className="apps">
                  {APPS.map((a) => (
                    <label key={a} className={'appchip ' + (!randomApps && apps.includes(a) ? 'on' : '')} style={{ borderColor: APP_COLOR[a] }}>
                      <input type="checkbox" disabled={randomApps} checked={!randomApps && apps.includes(a)} onChange={() => pickApps(toggle(apps, a))} />{a}
                      {!randomApps && apps[0] === a && apps.length > 1 && <span className="tag req">ilk başlatan</span>}
                    </label>
                  ))}
                </div>
                <div className="quick">
                  <button type="button" onClick={() => pickApps(recommended(sc, apps[0] ?? 'checkin'))} disabled={!sc}>Senaryonun önerdiği</button>
                  <button type="button" onClick={() => pickApps([...(apps.length ? apps : []), ...APPS.filter((a) => !apps.includes(a))])}>Tümü</button>
                  <label className="inline" title="Uygulamalar seed değerine göre rastgele seçilir; aynı seed aynı seçimi verir.">
                    <input type="checkbox" checked={randomApps} onChange={(e) => { setRandomApps(e.target.checked); setTouched(true); setPreview(null) }} />Rastgele seç</label>
                </div>
                {!randomApps && apps.length > 1 && (
                  <label className="inline">İlk başlatan uygulama
                    <select value={apps[0]} onChange={(e) => pickApps([e.target.value, ...apps.filter((a) => a !== e.target.value)])}>
                      {apps.map((a) => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </label>
                )}
                {sc && <p className="help">{sc.defaultAllTargets ? 'Bu senaryo için öneri: tüm uygulamalar.'
                  : sc.minApps > 1 ? `Bu senaryo en az ${sc.minApps} uygulama ister.`
                    : 'Tek uygulama yeterlidir; birden fazla seçerseniz yük artar.'}
                  {' '}Seçilen her uygulama aynı yükü kendi veritabanı bağlantılarıyla başlatır.{apps.length > 1 && !randomApps ? ` “İlk başlatan”, sonuçların esas alındığı uygulamadır (${sc.initiatorRole}).` : ''}</p>}
                <p className="help">Hedef her senaryoda paylaşılan PostgreSQL'dir{sc ? ` (yükün bindiği yer: ${sc.directTarget})` : ''}. Etki, aynı veritabanını kullanan diğer uygulamalara da yansıyabilir.</p>
              </fieldset>

              <fieldset><legend><span className="step">3</span> Ne kadar şiddetli olsun?</legend>
                <div className="modes">
                  {INTENSITIES.map((i) => (
                    <label key={i} title={INTENSITY_HELP[i]} className={'int-' + i.toLowerCase() + (f.intensity === i ? ' on' : '')}>
                      <input type="radio" name="int" checked={f.intensity === i} onChange={() => set('intensity', i)} />{INTENSITY_LABEL[i]}
                    </label>
                  ))}
                </div>
                <p className={'help ' + (severe ? 'warntext' : '')}>{INTENSITY_HELP[f.intensity]}</p>
                {f.intensity === 'CUSTOM' && (
                  <>
                    <div className="form-grid">
                      {CUSTOM_FIELDS.map(([k, l]) => (
                        <label key={k}>{l}<input type="number" placeholder="otomatik" value={(f.custom[k] as number | null | undefined) ?? ''}
                          onChange={(e) => setCustom(k, num(e.target.value))} /></label>
                      ))}
                    </div>
                    <h4>Güvenlik sınırları <small className="muted">(aşılırsa deney kendiliğinden durur)</small></h4>
                    <div className="form-grid">
                      {BUDGET_FIELDS.map(([k, l]) => (
                        <label key={k}>{l}<input type="number" placeholder="varsayılan"
                          value={((f.custom.budget ?? {}) as Record<string, number | null>)[k] ?? ''}
                          onChange={(e) => setBudget(k, num(e.target.value))} /></label>
                      ))}
                    </div>
                  </>
                )}
              </fieldset>
            </>
          ) : (
            <fieldset><legend>Rastgele plan</legend>
              <p className="help">Seçtiğiniz sınırlar içinde senaryolar ve uygulamalar rastgele seçilir, deneyler sıraya alınır. Tek senaryodaki güvenlik kuralları aynen geçerlidir.</p>
              <div className="form-grid">
                <label>Kaç deney<input type="number" min={1} max={10} value={rp.count} onChange={(e) => setRp({ ...rp, count: Number(e.target.value) })} /></label>
                <label>En yüksek yoğunluk<select value={rp.maxIntensity} onChange={(e) => setRp({ ...rp, maxIntensity: e.target.value as Intensity })}>
                  {INTENSITIES.slice(0, 4).map((i) => <option key={i} value={i}>{INTENSITY_LABEL[i]}</option>)}</select></label>
                <label>Deneyler arası bekleme (sn)<input type="number" value={rp.cooldownSec} onChange={(e) => setRp({ ...rp, cooldownSec: Number(e.target.value) })} /></label>
                <label title="Aynı seed aynı planı üretir">Seed<input value={rp.seed} placeholder="boş = rastgele" onChange={(e) => setRp({ ...rp, seed: e.target.value })} /></label>
                <label className="inline"><input type="checkbox" checked={rp.parallel} onChange={(e) => setRp({ ...rp, parallel: e.target.checked })} />Hepsini aynı anda çalıştır (tek birleşik deney)</label>
              </div>
              <h4>Yalnız bu senaryolardan seç <small className="muted">(işaretlenmezse tümü)</small></h4>
              <div className="apps wrap">{scenarios.map((s) => (
                <label key={s.id} title={s.name} className={'appchip ' + (rp.allowedScenarios.includes(s.id) ? 'on' : '')}>
                  <input type="checkbox" checked={rp.allowedScenarios.includes(s.id)}
                    onChange={() => setRp({ ...rp, allowedScenarios: toggle(rp.allowedScenarios, s.id) })} />{s.id} <small>{s.name}</small></label>))}
              </div>
              <h4>Bu senaryoları kullanma</h4>
              <div className="apps wrap">{scenarios.map((s) => (
                <label key={s.id} title={s.name} className={'appchip ' + (rp.excludedScenarios.includes(s.id) ? 'on bad' : '')}>
                  <input type="checkbox" checked={rp.excludedScenarios.includes(s.id)}
                    onChange={() => setRp({ ...rp, excludedScenarios: toggle(rp.excludedScenarios, s.id) })} />{s.id} <small>{s.name}</small></label>))}
              </div>
              <h4>Uygulamalar <small className="muted">(soldaki: kullan · sağdaki: kullanma)</small></h4>
              <div className="apps">{APPS.map((a) => (
                <span key={a} className="pair">
                  <label className={'appchip ' + (rp.allowedApps.includes(a) ? 'on' : '')}><input type="checkbox" checked={rp.allowedApps.includes(a)}
                    onChange={() => setRp({ ...rp, allowedApps: toggle(rp.allowedApps, a) })} />{a}</label>
                  <label className={'appchip ' + (rp.excludedApps.includes(a) ? 'on bad' : '')}><input type="checkbox" checked={rp.excludedApps.includes(a)}
                    onChange={() => setRp({ ...rp, excludedApps: toggle(rp.excludedApps, a) })} />kullanma</label>
                </span>))}
              </div>
            </fieldset>
          )}

          <fieldset><legend><span className="step">{tab === 'manual' ? 4 : '·'}</span> Ne kadar sürsün?</legend>
            <div className="timeline" aria-hidden="true">
              {DURATION_FIELDS.filter((x) => x.tone).map((x) => (
                <span key={x.k} className={'seg ' + x.tone} style={{ flexGrow: Math.max(1, d[x.k]) }} title={x.help}>
                  {x.label}<small>{humanDuration(d[x.k])}</small></span>
              ))}
            </div>
            <div className="form-grid">
              {DURATION_FIELDS.map((x) => (
                <label key={x.k} title={x.help}>{x.label} (sn)<input type="number" min={0} value={d[x.k]}
                  onChange={(e) => set('durations', { ...d, [x.k]: Number(e.target.value) })} /></label>
              ))}
            </div>
            <p className="help">Bir deney toplam yaklaşık <b>{humanDuration(total)}</b> sürer. Baseline, chaos öncesi olağan değerlerin ölçüldüğü süredir; SRE Agent karşılaştırması için en az 180 sn önerilir.</p>
          </fieldset>

          <details className="adv">
            <summary>Arka plan trafiği <small className="muted">{f.traffic.enabled ? `açık · uygulama başına ${f.traffic.rpsPerApp} rps` : 'kapalı'}</small></summary>
            <p className="help">Deney boyunca uygulamalara olağan kullanıcı istekleri gönderilir; böylece chaos'un gerçek isteklere etkisi ölçülebilir.</p>
            <div className="form-grid">
              <label className="inline"><input type="checkbox" checked={f.traffic.enabled}
                onChange={(e) => set('traffic', { ...f.traffic, enabled: e.target.checked })} />Arka plan trafiği gönder</label>
              {TRAFFIC_FIELDS.map(([k, l, h]) => (
                <label key={k} title={h}>{l}<input type="number" step="any" value={(f.traffic as unknown as Record<string, number>)[k]}
                  onChange={(e) => set('traffic', { ...f.traffic, [k]: Number(e.target.value) })} /></label>
              ))}
            </div>
            <h4>Trafik alan uygulamalar <small className="muted">(işaretlenmezse tümü)</small></h4>
            <div className="apps">{APPS.map((a) => (
              <label key={a} className={'appchip ' + (f.traffic.apps.includes(a) ? 'on' : '')}>
                <input type="checkbox" checked={f.traffic.apps.includes(a)}
                  onChange={() => set('traffic', { ...f.traffic, apps: toggle(f.traffic.apps, a) })} />{a}</label>))}
            </div>
          </details>

          {tab === 'manual' && (
            <details className="adv">
              <summary>Etiket ve tekrarlanabilirlik <small className="muted">isteğe bağlı</small></summary>
              <div className="form-grid">
                <label title="Geçmiş listesinde deneyi tanımanızı sağlar">Etiket<input value={f.label} placeholder="ör. lock testi 1" onChange={(e) => set('label', e.target.value)} /></label>
                <label title="Aynı seed, rastgele seçimleri aynı şekilde tekrarlar">Seed<input value={f.seed} placeholder="boş = rastgele" onChange={(e) => set('seed', e.target.value)} /></label>
              </div>
            </details>
          )}
        </div>

        <aside>
          {tab === 'manual' ? (
            <>
              <h3>Deney özeti</h3>
              <p className="sentence">
                <b>{randomApps ? 'Rastgele seçilen uygulamalar' : (apps.join(', ') || 'seçilmedi')}</b>,
                {' '}paylaşılan PostgreSQL üzerinde <b>{sc ? sc.name : f.scenarioId}</b> yükünü <b>{intensityLabel(f.intensity).toLowerCase()}</b> yoğunlukta başlatır.
                {' '}Toplam süre yaklaşık <b>{humanDuration(total)}</b>; chaos {humanDuration(d.durationSec)} uygulanır.
                {' '}Arka plan trafiği {f.traffic.enabled ? 'açık' : 'kapalı'}.
              </p>
              {problems.length > 0 && <ul className="problems">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
              {severe && problems.length === 0 && (
                <p className="help warntext">{intensityLabel(f.intensity)} yoğunluk: {INTENSITY_HELP[f.intensity]} Güvenlik sınırı aşılırsa deney kendiliğinden durur.</p>
              )}
              <div className="actions">
                <button onClick={doPreview} disabled={problems.length > 0}>Planı kontrol et</button>
                <button className="primary" disabled={busy || problems.length > 0} onClick={start}>Deneyi başlat</button>
              </div>
              <p className="help">“Planı kontrol et” deneyi başlatmaz; hedefleri, uygulanacak yükü ve başarı ölçütlerini gösterir.</p>
              {preview && !randomPlan && (
                <div className="preview">
                  <h4>Kimler katılıyor</h4>
                  <dl className="kv">
                    <dt>Hedef</dt><dd>Paylaşılan PostgreSQL</dd>
                    <dt>Yükü başlatan uygulamalar</dt><dd>{(preview.participants as string[]).map((a) => a === preview.initiator && (preview.participants as string[]).length > 1 ? `${a} (ilk başlatan)` : a).join(', ')}</dd>
                    <dt>Etkilenmesi beklenen</dt><dd>{(preview.expectedAffected as string[]).join(', ') || 'uygulama düzeyinde beklenmiyor (etki veritabanında)'}</dd>
                  </dl>
                  <h4>Ne yapılacak</h4>
                  <ul>{(preview.actions as Action[]).map((a) => <li key={String(a.part)}>{actionText(a)}</li>)}</ul>
                  <p>{preview.control
                    ? <>Yük, <b>{metricLabel(String((preview.control as { metric: string }).metric))}</b> ölçümü <b>{showValue((preview.control as { target: number }).target)}</b> değerine ulaşacak şekilde kendiliğinden ayarlanır.</>
                    : 'Yük sabit seviyede uygulanır.'}</p>
                  <h4>Başarı ölçütleri</h4>
                  <ul className="criteria">{(preview.criteria as Record<string, unknown>[]).map((c) => (
                    <li key={String(c.description)}><span className={'tag ' + (c.required ? 'req' : '')}>{c.required ? 'zorunlu' : 'bilgi'}</span> {String(c.description)}</li>))}</ul>
                  <p className="help">Sonuç yalnız “zorunlu” ölçütlere göre verilir.</p>
                  <h4>Uygulanacak değerler</h4>
                  <dl className="kv">{Object.entries((preview.parameters ?? {}) as Record<string, unknown>).map(([k, v]) => (
                    <div key={k} className="row"><dt>{paramLabel(k)}</dt><dd>{showValue(v)}</dd></div>))}
                    <div className="row"><dt>Seed</dt><dd>{String(preview.seed)}</dd></div>
                  </dl>
                </div>
              )}
            </>
          ) : (
            <>
              <h3>Rastgele plan</h3>
              <p className="sentence">
                <b>{rp.count}</b> deney, en fazla <b>{INTENSITY_LABEL[rp.maxIntensity].toLowerCase()}</b> yoğunlukta
                {rp.parallel ? ', hepsi aynı anda' : `, aralarında ${humanDuration(rp.cooldownSec)} bekleyerek`} sıraya alınır.
                Her biri yaklaşık <b>{humanDuration(total)}</b> sürer.
              </p>
              <div className="actions">
                <button className="primary" disabled={busy} onClick={startRandom}>Planı oluştur ve sıraya al</button>
              </div>
              <p className="help">Bu düğme deneyleri hemen sıraya alır; çalışan deney yoksa ilki başlar.</p>
              {randomPlan && (
                <div className="preview">
                  <h4>Sıraya alınan plan</h4>
                  <p>{randomPlan.experiments.length} deney · seed {String(randomPlan.seed)}. <a onClick={() => go('#/history')}>Geçmiş'te izle →</a></p>
                  <ol>{randomPlan.experiments.map((e, i) => (
                    <li key={i}>{String(e.scenarioId ?? '')} {String(e.scenarioName ?? '')}
                      {e.initiator ? <> · başlatan {String(e.initiator)}</> : null}
                      {e.intensity ? <> · {intensityLabel(String(e.intensity)).toLowerCase()}</> : null}</li>))}</ol>
                </div>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  )
}

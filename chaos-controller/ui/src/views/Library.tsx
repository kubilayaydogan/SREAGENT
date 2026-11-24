import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'
import type { Scenario } from '../types'

export function Library({ go }: { go: (h: string) => void }) {
  const [list, setList] = useState<Scenario[]>([])
  const [presets, setPresets] = useState<Record<string, Record<string, Record<string, unknown>>>>({})
  const [open, setOpen] = useState<string | null>(null)
  const [cat, setCat] = useState('')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api.scenarios().then(setList).catch((e) => setErr(String(e)))
    api.presets().then((p) => setPresets(p.scenarios)).catch(() => undefined)
  }, [])

  const cats = [...new Set(list.map((s) => s.category))]
  return (
    <div>
      {err && <p className="error">{err}</p>}
      <div className="filters">
        <label>Kategori <select value={cat} onChange={(e) => setCat(e.target.value)}>
          <option value="">tümü</option>{cats.map((c) => <option key={c}>{c}</option>)}
        </select></label>
        <span className="muted">{list.length} senaryo · yoğunluk değerleri ortamdan kalibre edilir</span>
      </div>
      <table className="table">
        <thead><tr><th>ID</th><th>Senaryo</th><th>Kategori</th><th>Initiator</th><th>Direct target</th><th /></tr></thead>
        <tbody>
          {list.filter((s) => !cat || s.category === cat).map((s) => (
            <Fragment key={s.id}>
              <tr onClick={() => setOpen(open === s.id ? null : s.id)} className="clickable">
                <td><b>{s.id}</b></td><td>{s.name}</td><td><span className="chip">{s.category}</span></td>
                <td>{s.initiatorRole}</td><td>{s.directTarget}</td>
                <td><button onClick={(e) => { e.stopPropagation(); go('#/builder/' + s.id) }}>Kur →</button></td>
              </tr>
              {open === s.id && (
                <tr className="detail"><td colSpan={6}>
                  <p>{s.description}</p>
                  <div className="cols">
                    <div><h4>Prerequisites</h4><ul>{s.prerequisites.map((x) => <li key={x}>{x}</li>)}</ul>
                      <h4>Cleanup</h4><p>{s.cleanup}</p></div>
                    <div><h4>Expected impact</h4><ul>{s.expectedImpact.map((x) => <li key={x}>{x}</li>)}</ul></div>
                    <div><h4>Abort conditions</h4><ul>{s.abortConditions.map((x) => <li key={x}>{x}</li>)}</ul>
                      <p className="muted">min. uygulama: {s.minApps}{s.defaultAllTargets ? ' · varsayılan: tüm uygulamalar' : ''}</p></div>
                  </div>
                  {presets[s.id] && (
                    <>
                      <h4>Kalibre yoğunluklar (ilk uygulama initiator iken)</h4>
                      <table className="table small">
                        <thead><tr><th>Intensity</th><th>Parametreler</th><th>Control</th><th>Max workers</th></tr></thead>
                        <tbody>{Object.entries(presets[s.id]).map(([k, v]) => (
                          <tr key={k}><td>{k}</td>
                            <td><code>{JSON.stringify(Object.fromEntries(Object.entries(v).filter(([kk]) => kk !== 'control' && kk !== 'maxWorkers')))}</code></td>
                            <td>{v.control ? `${(v.control as { metric: string }).metric} → ${(v.control as { target: number }).target}` : 'sabit'}</td>
                            <td>{String(v.maxWorkers ?? '—')}</td></tr>
                        ))}</tbody>
                      </table>
                    </>
                  )}
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api } from '../api'
import { categoryLabel, intensityLabel, paramLabel, showValue } from '../labels'
import type { Scenario } from '../types'

type Presets = Record<string, Record<string, Record<string, unknown>>>

function PresetTable({ p }: { p: Record<string, Record<string, unknown>> }) {
  return (
    <table className="table small">
      <thead><tr><th>Yoğunluk</th><th>Uygulanan değerler</th><th title="Yük, bu ölçüm hedefe ulaşacak şekilde kendiliğinden ayarlanır">Hedeflenen ölçüm</th></tr></thead>
      <tbody>{Object.entries(p).map(([k, v]) => {
        const params = Object.entries(v).filter(([kk]) => kk !== 'control')
        const c = v.control as { metric?: string; target?: number } | undefined
        return (
          <tr key={k}><td><b>{intensityLabel(k)}</b></td>
            <td>{params.map(([kk, x]) => <span key={kk} className="kvchip">{paramLabel(kk)}: <b>{showValue(x)}</b></span>)}</td>
            <td>{c ? `${c.metric} → ${showValue(c.target)}` : 'sabit seviye'}</td></tr>
        )
      })}</tbody>
    </table>
  )
}

export function Library({ go }: { go: (h: string) => void }) {
  const [list, setList] = useState<Scenario[]>([])
  const [presets, setPresets] = useState<Presets>({})
  const [open, setOpen] = useState<string | null>(null)
  const [cat, setCat] = useState('')
  const [q, setQ] = useState('')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api.scenarios().then(setList).catch((e) => setErr(String(e)))
    api.presets().then((p) => setPresets(p.scenarios)).catch(() => undefined)
  }, [])

  const cats = [...new Set(list.map((s) => s.category))]
  const needle = q.trim().toLowerCase()
  const rows = list.filter((s) => (!cat || s.category === cat)
    && (!needle || (s.id + ' ' + s.name + ' ' + s.description).toLowerCase().includes(needle)))

  return (
    <div>
      <div className="pagehead">
        <div>
          <h2>Senaryolar</h2>
          <p className="muted">Her senaryo sisteme belirli bir sorunu bilerek uygular. Ne yaptığını okuyun, sonra “Bu senaryoyu kur” ile deneyi hazırlayın.</p>
        </div>
      </div>
      {err && <p className="error">Senaryolar alınamadı: {err}</p>}
      <div className="filters">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Senaryo ara (ör. lock, pool, CPU)" />
        <div className="chips">
          <button className={'chipbtn ' + (cat === '' ? 'on' : '')} onClick={() => setCat('')}>Tümü ({list.length})</button>
          {cats.map((c) => (
            <button key={c} className={'chipbtn ' + (cat === c ? 'on' : '')} onClick={() => setCat(c)}>
              {categoryLabel(c)} ({list.filter((s) => s.category === c).length})</button>
          ))}
        </div>
      </div>
      {!rows.length && list.length > 0 && <p className="muted">Bu aramaya uyan senaryo yok.</p>}
      <section className="scenarios">
        {rows.map((s) => (
          <article key={s.id} className={'scenario ' + (open === s.id ? 'open' : '')}>
            <header>
              <span className="sid">{s.id}</span>
              <h3>{s.name}</h3>
              <span className="chip">{categoryLabel(s.category)}</span>
            </header>
            <p>{s.description}</p>
            <dl className="kv">
              <dt>Yükü başlatan</dt><dd>{s.initiatorRole}</dd>
              <dt>Doğrudan hedef</dt><dd>{s.directTarget}</dd>
            </dl>
            {open === s.id && (
              <div className="detail">
                <h4>Beklenen etki</h4><ul>{s.expectedImpact.map((x) => <li key={x}>{x}</li>)}</ul>
                <h4>Ön koşullar</h4><ul>{s.prerequisites.map((x) => <li key={x}>{x}</li>)}</ul>
                <h4>Deney kendiliğinden durur, eğer</h4><ul>{s.abortConditions.map((x) => <li key={x}>{x}</li>)}</ul>
                <h4>Bittiğinde temizlik</h4><p>{s.cleanup}</p>
                <p className="muted small">En az {s.minApps} uygulama gerekir{s.defaultAllTargets ? ' · varsayılan hedef: tüm uygulamalar' : ''}.</p>
                {presets[s.id] && (
                  <>
                    <h4>Yoğunluk seviyeleri (bu ortama göre ayarlanmış)</h4>
                    <PresetTable p={presets[s.id]} />
                  </>
                )}
              </div>
            )}
            <footer>
              <button onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? 'Ayrıntıyı gizle' : 'Ayrıntı'}</button>
              <button className="primary" onClick={() => go('#/builder/' + s.id)}>Bu senaryoyu kur →</button>
            </footer>
          </article>
        ))}
      </section>
    </div>
  )
}

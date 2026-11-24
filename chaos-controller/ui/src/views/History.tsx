import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Summary } from '../types'

export function History({ go }: { go: (h: string) => void }) {
  const [data, setData] = useState<{ experiments: Summary[]; queue: Summary[]; active: Summary | null } | null>(null)
  const [q, setQ] = useState('')
  const [err, setErr] = useState<string | null>(null)

  const load = () => api.list().then(setData).catch((e) => setErr(String(e)))
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [])

  async function rerun(id: string) {
    try { const s = await api.rerun(id); go('#/live/' + s.id) } catch (e) { setErr(String(e)) }
  }
  if (!data) return <p className="muted">{err ?? 'yükleniyor…'}</p>
  const rows = data.experiments.filter((e) => !q || JSON.stringify(e).toLowerCase().includes(q.toLowerCase()))
  return (
    <div>
      {err && <p className="error">{err}</p>}
      {data.queue.length > 0 && <p className="banner">Kuyrukta {data.queue.length} deney: {data.queue.map((x) => x.scenarioId).join(', ')}</p>}
      <div className="filters"><label>Ara <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="senaryo, uygulama, durum…" /></label>
        <span className="muted">{rows.length} deney</span></div>
      <table className="table">
        <thead><tr><th>ID</th><th>Senaryo</th><th>Initiator → targets</th><th>Intensity</th><th>Durum</th><th>Verdict</th><th>Recovery</th><th /></tr></thead>
        <tbody>{rows.map((e) => (
          <tr key={e.id}>
            <td><a onClick={() => go('#/live/' + e.id)}>{e.id}</a><br /><small className="muted">{e.createdAt.slice(0, 19).replace('T', ' ')}</small></td>
            <td>{e.scenarioId} {e.scenarioName}{e.label ? <><br /><small className="muted">{e.label}</small></> : null}</td>
            <td>{e.initiator} → {e.targets.join(', ')}</td>
            <td>{e.intensity}</td>
            <td><span className={'state ' + e.state}>{e.state}</span></td>
            <td><span className={'verdict ' + (e.verdict ?? '')}>{e.verdict ?? '—'}</span></td>
            <td>{e.recovered === null ? '—' : e.recovered ? '✔' : '✘'}</td>
            <td><button onClick={() => rerun(e.id)}>Tekrar çalıştır</button></td>
          </tr>))}
        </tbody>
      </table>
    </div>
  )
}

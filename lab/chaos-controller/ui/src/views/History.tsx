import { useEffect, useState } from 'react'
import { api } from '../api'
import { isTerminal, localDateTime, toMs } from '../lib'
import { humanDuration, intensityLabel, stateLabel, verdictLabel } from '../labels'
import type { Summary } from '../types'

type Filter = 'all' | 'running' | 'confirmed' | 'notobserved' | 'problem'
const FILTERS: [Filter, string][] = [
  ['all', 'Tümü'], ['running', 'Çalışan'], ['confirmed', 'Etki doğrulandı'], ['notobserved', 'Etki gözlenmedi'],
  ['problem', 'Başarısız ya da durdurulan'],
]

function matches(e: Summary, f: Filter): boolean {
  switch (f) {
    case 'running': return !isTerminal(e.state)
    case 'confirmed': return e.verdict === 'IMPACT_CONFIRMED'
    case 'notobserved': return e.verdict === 'IMPACT_NOT_OBSERVED'
    case 'problem': return ['FAILED', 'ABORTED', 'TIMED_OUT'].includes(e.state)
    default: return true
  }
}

export function History({ go }: { go: (h: string) => void }) {
  const [data, setData] = useState<{ experiments: Summary[]; queue: Summary[]; active: Summary | null } | null>(null)
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [err, setErr] = useState<string | null>(null)

  const load = () => api.list().then((d) => { setData(d); setErr(null) }).catch((e) => setErr(String(e)))
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [])

  async function rerun(id: string) {
    try { const s = await api.rerun(id); go('#/live/' + s.id) } catch (e) { setErr(String(e)) }
  }
  if (!data) return <p className="muted">{err ?? 'Yükleniyor…'}</p>
  const needle = q.trim().toLowerCase()
  const text = (e: Summary) => [e.id, e.scenarioId, e.scenarioName, e.initiator, e.targets.join(' '), e.label ?? '',
    stateLabel(e.state), verdictLabel(e.verdict), intensityLabel(e.intensity)].join(' ').toLowerCase()
  const rows = data.experiments.filter((e) => matches(e, filter) && (!needle || text(e).includes(needle)))

  return (
    <div>
      <div className="pagehead">
        <div>
          <h2>Geçmiş</h2>
          <p className="muted">Çalıştırılan deneyler, en yenisi üstte. Bir satıra tıklayınca deneyin ölçümleri ve sonucu açılır.</p>
        </div>
        <button className="primary" onClick={() => go('#/builder')}>Yeni deney kur</button>
      </div>
      {err && <p className="error">Liste alınamadı: {err}</p>}
      {data.queue.length > 0 && (
        <p className="banner">Sırada {data.queue.length} deney bekliyor: {data.queue.map((x) => `${x.scenarioId} ${x.scenarioName}`).join(', ')}</p>
      )}
      <div className="filters">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ara: senaryo, uygulama, etiket…" />
        <div className="chips">
          {FILTERS.map(([k, l]) => (
            <button key={k} className={'chipbtn ' + (filter === k ? 'on' : '')} onClick={() => setFilter(k)}>
              {l} ({data.experiments.filter((e) => matches(e, k)).length})</button>
          ))}
        </div>
      </div>
      <table className="table">
        <thead><tr><th>Başlangıç</th><th>Senaryo</th><th title="Yükü başlatan uygulama → doğrudan hedefler">Başlatan → hedef</th><th>Yoğunluk</th>
          <th>Durum</th><th title="Senaryo beklenen etkiyi üretti mi">Sonuç</th>
          <th title="Deney sonrası sistem baseline değerlerine döndü mü">Toparlanma</th><th>Süre</th><th /></tr></thead>
        <tbody>{rows.map((e) => {
          const sec = e.startedAt && e.endedAt ? (toMs(e.endedAt) - toMs(e.startedAt)) / 1000 : null
          return (
            <tr key={e.id} className="clickable" onClick={() => go('#/live/' + e.id)}>
              <td>{localDateTime(e.startedAt ?? e.createdAt)}<br /><small className="muted">{e.id}</small></td>
              <td><b>{e.scenarioName}</b> <small className="muted">{e.scenarioId}</small>{e.label ? <><br /><small className="muted">{e.label}</small></> : null}</td>
              <td>{e.initiator} → {e.targets.join(', ') || '—'}</td>
              <td>{intensityLabel(e.intensity)}</td>
              <td title={e.stateReason ?? ''}><span className={'state ' + e.state}>{stateLabel(e.state)}</span></td>
              <td><span className={'verdict ' + (e.verdict ?? '')}>{verdictLabel(e.verdict)}</span></td>
              <td>{e.recovered === null ? '—' : e.recovered ? <span className="oktext">✔ döndü</span> : <span className="badtext">✘ doğrulanamadı</span>}</td>
              <td>{humanDuration(sec)}</td>
              <td><button onClick={(ev) => { ev.stopPropagation(); rerun(e.id) }} title="Aynı ayarlarla yeni bir deney başlatır">Tekrar çalıştır</button></td>
            </tr>
          )
        })}
          {!rows.length && <tr><td colSpan={9} className="muted">{data.experiments.length ? 'Bu filtreye uyan deney yok.' : 'Henüz deney çalıştırılmadı.'}</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

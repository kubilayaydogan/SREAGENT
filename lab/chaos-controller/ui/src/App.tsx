import { useEffect, useState } from 'react'
import { api } from './api'
import { stateLabel } from './labels'
import type { Summary } from './types'
import { Builder } from './views/Builder'
import { History } from './views/History'
import { Library } from './views/Library'
import { Live } from './views/Live'
import { Overview } from './views/Overview'

export function parseRoute(hash: string): { view: string; arg?: string } {
  const [, view = 'overview', arg] = hash.replace(/^#\/?/, '#/').split('/')
  return { view: view || 'overview', arg: arg ? decodeURIComponent(arg) : undefined }
}

const NAV: [string, string, string][] = [
  ['overview', 'Genel durum', 'Uygulamaların ve PostgreSQL\'in şu anki durumu'],
  ['library', 'Senaryolar', 'Çalıştırılabilecek chaos senaryoları ve ne yaptıkları'],
  ['builder', 'Yeni deney', 'Bir senaryo seçip deney başlatın'],
  ['history', 'Geçmiş', 'Önceki deneyler ve sonuçları'],
]

export function App() {
  const [hash, setHash] = useState(window.location.hash || '#/overview')
  const [active, setActive] = useState<Summary | null>(null)
  const [queued, setQueued] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/overview')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  useEffect(() => {
    let alive = true
    const tick = () => api.list().then((d) => { if (alive) { setActive(d.active); setQueued(d.queue.length) } }).catch(() => undefined)
    tick()
    const t = setInterval(tick, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])
  const go = (h: string) => { window.location.hash = h }
  const { view, arg } = parseRoute(hash)

  async function emergency() {
    if (!window.confirm('Acil durdurma: çalışan deney ve sıradaki tüm deneyler iptal edilir, tüm uygulamalarda temizlik çalışır.\n\nDevam edilsin mi?')) return
    try {
      await api.emergency()
      setNotice('Acil durdurma gönderildi: deneyler iptal edildi, temizlik çalışıyor.')
    } catch (e) { setNotice('Acil durdurma başarısız: ' + String(e)) }
  }

  return (
    <>
      <header className="top">
        <h1>Chaos Lab</h1>
        <nav>
          {NAV.map(([k, l, t]) => (
            <a key={k} title={t} className={view === k || (k === 'history' && view === 'live') ? 'on' : ''} href={'#/' + k}>{l}</a>
          ))}
        </nav>
        {active ? (
          <a className="pill busy" href={'#/live/' + encodeURIComponent(active.id)} title="Çalışan deneyi aç">
            <span className="dot" /> Deney çalışıyor: {active.scenarioName} · {stateLabel(active.state)}
            {queued > 0 ? ` · sırada ${queued}` : ''}
          </a>
        ) : (
          <span className="pill idle" title="Şu anda çalışan deney yok"><span className="dot" /> Çalışan deney yok</span>
        )}
        <button className="danger" onClick={emergency}
          title="Çalışan ve sıradaki tüm deneyleri iptal eder, tüm uygulamalarda temizlik çalıştırır">Acil durdur</button>
      </header>
      <main>
        {notice && <p className="banner" onClick={() => setNotice(null)}>{notice}</p>}
        {view === 'overview' && <Overview go={go} />}
        {view === 'library' && <Library go={go} />}
        {view === 'builder' && <Builder preset={arg} go={go} />}
        {view === 'live' && arg && <Live id={arg} go={go} />}
        {view === 'history' && <History go={go} />}
        <footer className="foot">
          Saatler Türkiye saatiyledir (TSİ). “—” o değerin ölçülemediğini gösterir. <a href="/classic">Klasik panel</a>
        </footer>
      </main>
    </>
  )
}

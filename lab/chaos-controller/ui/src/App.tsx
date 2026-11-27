import { useEffect, useState } from 'react'
import { api } from './api'
import { Builder } from './views/Builder'
import { History } from './views/History'
import { Library } from './views/Library'
import { Live } from './views/Live'
import { Overview } from './views/Overview'

export function parseRoute(hash: string): { view: string; arg?: string } {
  const [, view = 'overview', arg] = hash.replace(/^#\/?/, '#/').split('/')
  return { view: view || 'overview', arg: arg ? decodeURIComponent(arg) : undefined }
}

export function App() {
  const [hash, setHash] = useState(window.location.hash || '#/overview')
  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/overview')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const go = (h: string) => { window.location.hash = h }
  const { view, arg } = parseRoute(hash)

  async function emergency() {
    if (!window.confirm('EMERGENCY STOP: aktif deney ve tüm kuyruk iptal edilir, tüm uygulamalarda cleanup çalışır. Devam?')) return
    try { await api.emergency() } catch (e) { window.alert(String(e)) }
  }

  const nav = [['overview', 'Overview'], ['library', 'Scenario Library'], ['builder', 'Experiment Builder'], ['history', 'History']]
  return (
    <>
      <header className="top">
        <h1>⚡ Chaos Controller <small>v2</small></h1>
        <nav>{nav.map(([k, l]) => <a key={k} className={view === k ? 'on' : ''} href={'#/' + k}>{l}</a>)}
          <a href="/classic">Klasik panel</a></nav>
        <button className="danger" onClick={emergency}>EMERGENCY STOP</button>
      </header>
      <main>
        {view === 'overview' && <Overview go={go} />}
        {view === 'library' && <Library go={go} />}
        {view === 'builder' && <Builder preset={arg} go={go} />}
        {view === 'live' && arg && <Live id={arg} go={go} />}
        {view === 'history' && <History go={go} />}
      </main>
    </>
  )
}

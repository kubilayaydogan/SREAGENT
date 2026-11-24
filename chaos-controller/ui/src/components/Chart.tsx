import { useState } from 'react'
import { fmt } from '../lib'

export interface Line { name: string; color: string; values: (number | null)[] }

/**
 * Tek eksenli küçük çizgi grafiği (small multiple). Null değerler boşluk olarak çizilir (uydurma interpolasyon yok).
 * Hover: dikey crosshair + tüm serilerin o andaki değeri. Faz bantları arka planda gösterilir.
 */
export function Chart(props: {
  title: string
  unit?: string
  lines: Line[]
  labels?: string[]
  bands?: { from: number; to: number; label: string; tone: string }[]
  target?: number | null
  height?: number
}) {
  const { title, unit = '', lines, labels = [], bands = [], target = null, height = 120 } = props
  const [hover, setHover] = useState<number | null>(null)
  const W = 360
  const H = height
  const P = { l: 34, r: 8, t: 8, b: 16 }
  const n = Math.max(...lines.map((l) => l.values.length), 2)
  const all = lines.flatMap((l) => l.values.filter((v): v is number => v !== null))
  if (target !== null) all.push(target)
  const max = all.length ? Math.max(...all) * 1.1 || 1 : 1
  const x = (i: number) => P.l + ((W - P.l - P.r) * i) / (n - 1)
  const y = (v: number) => H - P.b - ((H - P.t - P.b) * v) / max
  const path = (vals: (number | null)[]) => {
    let d = ''
    let pen = false
    vals.forEach((v, i) => {
      if (v === null) { pen = false; return }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
      pen = true
    })
    return d
  }
  return (
    <figure className="chart">
      <figcaption>
        <span>{title}</span>
        <span className="legend">
          {lines.length > 1 && lines.map((l) => (
            <span key={l.name}><i style={{ background: l.color }} />{l.name}</span>
          ))}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const px = ((e.clientX - r.left) / r.width) * W
          const i = Math.round(((px - P.l) / (W - P.l - P.r)) * (n - 1))
          setHover(i >= 0 && i < n ? i : null)
        }}>
        {bands.map((b) => (
          <rect key={b.label + b.from} x={x(b.from)} y={P.t} width={Math.max(1, x(b.to) - x(b.from))}
            height={H - P.t - P.b} className={'band ' + b.tone}><title>{b.label}</title></rect>
        ))}
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={P.l} x2={W - P.r} y1={y(max * f / 1.1)} y2={y(max * f / 1.1)} className="grid" />
            <text x={P.l - 4} y={y(max * f / 1.1) + 3} className="axis" textAnchor="end">{fmt(max * f / 1.1, 0)}</text>
          </g>
        ))}
        {target !== null && <line x1={P.l} x2={W - P.r} y1={y(target)} y2={y(target)} className="target" />}
        {lines.map((l) => <path key={l.name} d={path(l.values)} stroke={l.color} className="line" />)}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={P.t} y2={H - P.b} className="cross" />}
      </svg>
      {hover !== null && (
        <div className="tip">
          {labels[hover] && <b>{labels[hover]}</b>}
          {lines.map((l) => (
            <span key={l.name}><i style={{ background: l.color }} />{l.name}: {fmt(l.values[hover] ?? null, 1, unit)}</span>
          ))}
          {target !== null && <span>target: {fmt(target, 1, unit)}</span>}
        </div>
      )}
    </figure>
  )
}

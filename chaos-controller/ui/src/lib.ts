import type { Custom, Durations, ExperimentRequest, Intensity, Sample, State, TargetMode, Traffic } from './types'
import { TERMINAL } from './types'

export const APPS = ['reservation', 'checkin', 'flightops', 'baggage'] as const

/** Kategorik renkler: uygulamaya sabit atanır (sıraya/filtreye göre değişmez). */
export const APP_COLOR: Record<string, string> = {
  reservation: '#2a6fdb',
  checkin: '#d9822b',
  flightops: '#13906b',
  baggage: '#8f5bd6',
}

/** Ölçülmemiş değer "—" görünür; asla 0 gibi gösterilmez. */
export function fmt(v: number | null | undefined, digits = 1, unit = ''): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const n = Math.abs(v) >= 100 ? Math.round(v).toString() : v.toFixed(digits)
  return unit ? `${n} ${unit}` : n
}

export function isTerminal(s: State | string | null | undefined): boolean {
  return !!s && (TERMINAL as string[]).includes(s)
}

export const DEFAULT_DURATIONS: Durations = { baselineSec: 30, rampSec: 30, durationSec: 120, recoverySec: 90, maxDurationSec: 0 }
export const DEFAULT_TRAFFIC: Traffic = {
  enabled: true, apps: [], rpsPerApp: 6, users: 8, readWriteRatio: 0.8, timeoutMs: 3000, retries: 1,
  retryBackoffMs: 100, thinkTimeMs: 50, maxTotalRps: 200, hotRows: false,
}

export interface BuilderForm {
  scenarioId: string
  initiator: string
  targetMode: TargetMode
  targets: string[]
  intensity: Intensity
  custom: Custom
  durations: Durations
  traffic: Traffic
  seed: string
  label: string
}

/** Boş custom alanları gönderilmez (backend kalibre varsayılanı kullanır). */
export function cleanCustom(c: Custom): Custom | null {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(c)) {
    if (k === 'budget') {
      const b = Object.fromEntries(Object.entries((v ?? {}) as Record<string, unknown>)
        .filter(([, x]) => x !== null && x !== undefined && x !== ''))
      if (Object.keys(b).length) out.budget = b
    } else if (v !== null && v !== undefined && v !== '' && !Number.isNaN(v as number)) {
      out[k] = v
    }
  }
  return Object.keys(out).length ? (out as Custom) : null
}

export function buildRequest(f: BuilderForm, clientRequestId: string): ExperimentRequest {
  const seed = f.seed.trim() === '' ? null : Number(f.seed)
  return {
    scenarioId: f.scenarioId,
    initiator: f.targetMode === 'RANDOM' && !f.initiator ? null : f.initiator || null,
    targetMode: f.targetMode,
    targets: f.targetMode === 'MULTI' || f.targetMode === 'SINGLE' ? f.targets : [],
    intensity: f.intensity,
    custom: f.intensity === 'CUSTOM' ? cleanCustom(f.custom) : null,
    durations: f.durations,
    traffic: f.traffic,
    seed: seed !== null && Number.isFinite(seed) ? seed : null,
    label: f.label.trim() || null,
    clientRequestId,
  }
}

/** Bir örnekten seçici ile seri; ölçülmemiş noktalar null (çizgide boşluk). */
export function series(samples: Sample[], pick: (s: Sample) => number | null | undefined): (number | null)[] {
  return samples.map((s) => {
    const v = pick(s)
    return v === undefined || v === null || Number.isNaN(v) ? null : v
  })
}

/** Uygulamanın deneydeki rolleri: initiator / direct target / observed affected ayrı gösterilir. */
export function rolesOf(app: string, initiator: string | null, targets: string[], observedAffected: string[] = []): string[] {
  const r: string[] = []
  if (app === initiator) r.push('initiator')
  if (targets.includes(app) && app !== initiator) r.push('direct target')
  if (observedAffected.includes(app)) r.push('observed affected')
  return r
}

/** Faz ilerleme yüzdesi (planlanan sürelere göre). */
export function phaseProgress(state: State, elapsedInPhaseSec: number, d: Durations): number {
  const len: Partial<Record<State, number>> = {
    BASELINING: d.baselineSec, RAMPING_UP: d.rampSec, RUNNING: d.durationSec, RECOVERING: d.recoverySec,
  }
  const l = len[state]
  if (!l) return isTerminal(state) ? 100 : 0
  return Math.max(0, Math.min(100, (100 * elapsedInPhaseSec) / l))
}

export function newClientRequestId(): string {
  return 'ui-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8)
}

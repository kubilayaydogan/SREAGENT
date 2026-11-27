import type { Experiment, ExperimentRequest, Overview, Sample, Scenario, Summary } from './types'

// Aynı origin; HTTP Basic kimlik bilgisini tarayıcı yönetir (BasicAuthFilter).
async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    const errs = data && Array.isArray(data.errors) ? data.errors.join('; ') : `HTTP ${res.status}`
    throw new Error(errs)
  }
  return data as T
}

export const api = {
  overview: () => call<Overview>('GET', '/api/v2/overview'),
  history: (max = 150) => call<Sample[]>('GET', `/api/v2/metrics/history?max=${max}`),
  scenarios: () => call<Scenario[]>('GET', '/api/v2/scenarios'),
  presets: (initiator?: string) =>
    call<{ calibration: Record<string, unknown>; scenarios: Record<string, Record<string, Record<string, unknown>>> }>(
      'GET', '/api/v2/intensity-presets' + (initiator ? `?initiator=${encodeURIComponent(initiator)}` : '')),
  preview: (r: ExperimentRequest) => call<Record<string, unknown>>('POST', '/api/v2/experiments/preview', r),
  submit: (r: ExperimentRequest) => call<Summary>('POST', '/api/v2/experiments', r),
  list: () => call<{ experiments: Summary[]; queue: Summary[]; active: Summary | null }>('GET', '/api/v2/experiments'),
  get: (id: string, samples = true) =>
    call<Experiment>('GET', `/api/v2/experiments/${encodeURIComponent(id)}?samples=${samples}`),
  live: (id: string, since: number) =>
    call<{ state: string; stateReason: string; transitions: Experiment['transitions']; samples: Sample[];
      logs: Experiment['logs']; report: Experiment['report'] }>(
      'GET', `/api/v2/experiments/${encodeURIComponent(id)}/live?since=${since}`),
  stop: (id: string) => call<unknown>('POST', `/api/v2/experiments/${encodeURIComponent(id)}/stop`),
  rerun: (id: string) => call<Summary>('POST', `/api/v2/experiments/${encodeURIComponent(id)}/rerun`),
  emergency: () => call<Record<string, unknown>>('POST', '/api/v2/emergency-stop'),
  randomPlan: (p: unknown) => call<{ planId: string; seed: string; experiments: unknown[] }>('POST', '/api/v2/random-plans', p),
}

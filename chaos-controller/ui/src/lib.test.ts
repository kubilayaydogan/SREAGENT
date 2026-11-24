import { describe, expect, it } from 'vitest'
import { parseRoute } from './App'
import { DEFAULT_DURATIONS, DEFAULT_TRAFFIC, buildRequest, cleanCustom, fmt, isTerminal, phaseProgress, rolesOf, series } from './lib'
import type { Sample } from './types'

const form = {
  scenarioId: 'S01', initiator: 'checkin', targetMode: 'AUTO' as const, targets: ['baggage'], intensity: 'MEDIUM' as const,
  custom: { workers: 4 }, durations: DEFAULT_DURATIONS, traffic: DEFAULT_TRAFFIC, seed: '', label: '  ',
}

describe('fmt', () => {
  it('ölçülmemiş değeri — gösterir, 0 değil', () => {
    expect(fmt(null)).toBe('—')
    expect(fmt(undefined)).toBe('—')
    expect(fmt(NaN)).toBe('—')
    expect(fmt(0)).toBe('0.0')
    expect(fmt(123.456, 1, 'ms')).toBe('123 ms')
    expect(fmt(1.234, 2, '%')).toBe('1.23 %')
  })
})

describe('buildRequest', () => {
  it('AUTO modunda targets göndermez, custom yalnız CUSTOM yoğunlukta', () => {
    const r = buildRequest(form, 'x')
    expect(r.targets).toEqual([])
    expect(r.custom).toBeNull()
    expect(r.label).toBeNull()
    expect(r.seed).toBeNull()
    expect(r.clientRequestId).toBe('x')
  })
  it('MULTI hedefleri ve CUSTOM parametreleri korur, seed sayıya çevrilir', () => {
    const r = buildRequest({ ...form, targetMode: 'MULTI', intensity: 'CUSTOM', seed: '42',
      custom: { workers: 4, rps: null, budget: { maxDbCpuPct: 80, maxAppHeapPct: null } } }, 'y')
    expect(r.targets).toEqual(['baggage'])
    expect(r.seed).toBe(42)
    expect(r.custom).toEqual({ workers: 4, budget: { maxDbCpuPct: 80 } })
  })
  it('geçersiz seed null olur', () => {
    expect(buildRequest({ ...form, seed: 'abc' }, 'z').seed).toBeNull()
  })
})

describe('cleanCustom', () => {
  it('tamamen boşsa null', () => {
    expect(cleanCustom({ workers: null, budget: { maxDbCpuPct: null } })).toBeNull()
  })
})

describe('rolesOf', () => {
  it('initiator, direct target ve observed affected ayrıdır', () => {
    expect(rolesOf('checkin', 'checkin', ['checkin', 'baggage'], [])).toEqual(['initiator'])
    expect(rolesOf('baggage', 'checkin', ['checkin', 'baggage'], ['baggage'])).toEqual(['direct target', 'observed affected'])
    expect(rolesOf('flightops', 'checkin', ['baggage'], ['flightops'])).toEqual(['observed affected'])
    expect(rolesOf('reservation', 'checkin', ['baggage'])).toEqual([])
  })
})

describe('series', () => {
  it('ölçülmeyen noktalar null kalır (interpolasyon yok)', () => {
    const s = [{ db: { cpuPct: 5 } }, { db: null }, { db: { cpuPct: NaN } }, { db: { cpuPct: 7 } }] as unknown as Sample[]
    expect(series(s, (x) => x.db?.cpuPct)).toEqual([5, null, null, 7])
  })
})

describe('phase helpers', () => {
  it('terminal durumlar', () => {
    expect(isTerminal('COMPLETED')).toBe(true)
    expect(isTerminal('TIMED_OUT')).toBe(true)
    expect(isTerminal('RUNNING')).toBe(false)
    expect(isTerminal(null)).toBe(false)
  })
  it('faz ilerlemesi planlanan süreye göre sınırlanır', () => {
    expect(phaseProgress('RUNNING', 60, DEFAULT_DURATIONS)).toBe(50)
    expect(phaseProgress('RUNNING', 999, DEFAULT_DURATIONS)).toBe(100)
    expect(phaseProgress('COMPLETED', 0, DEFAULT_DURATIONS)).toBe(100)
    expect(phaseProgress('VALIDATING', 3, DEFAULT_DURATIONS)).toBe(0)
  })
})

describe('parseRoute', () => {
  it('hash rotalarını çözer', () => {
    expect(parseRoute('')).toEqual({ view: 'overview', arg: undefined })
    expect(parseRoute('#/live/v2-1')).toEqual({ view: 'live', arg: 'v2-1' })
    expect(parseRoute('#/builder/S07')).toEqual({ view: 'builder', arg: 'S07' })
  })
})

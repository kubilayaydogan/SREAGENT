import { describe, expect, it } from 'vitest'
import { parseRoute } from './App'
import { DEFAULT_DURATIONS, DEFAULT_TRAFFIC, agentWindows, buildRequest, cleanCustom, fmt, isTerminal, localDateTime, localTime, phaseProgress, rolesOf, series } from './lib'
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

describe('yerel saat gösterimi', () => {
  it('UTC ISO değerini İstanbul saatine (+03) çevirir', () => {
    expect(localTime('2026-11-24T21:22:37.372105572Z')).toBe('00:22:37')
    expect(localDateTime('2026-11-24T21:22:37.372Z')).toBe('2026-11-25 00:22:37')
    expect(localTime(null)).toBe('')
    expect(localTime('bozuk')).toBe('')
  })
  it('agent pencerelerini fazlardan tam dakikaya içe yuvarlar', () => {
    const tr = [
      { state: 'BASELINING', at: '2026-11-24T21:22:40Z' },
      { state: 'RAMPING_UP', at: '2026-11-24T21:26:10Z' },
      { state: 'RUNNING', at: '2026-11-24T21:26:40Z' },
      { state: 'RECOVERING', at: '2026-11-24T21:31:10Z' },
    ]
    expect(agentWindows(tr)).toEqual({
      baseline: ['2026-11-25 00:23', '2026-11-25 00:26'],
      chaos: ['2026-11-25 00:27', '2026-11-25 00:31'],
    })
    expect(agentWindows(tr.slice(0, 2)).chaos).toBeNull()
  })
})

import { humanDuration, intensityLabel, showValue, stateLabel, verdictLabel } from './labels'

describe('okunur etiketler', () => {
  it('durum, yoğunluk ve sonuç kodlarını çevirir; bilinmeyeni olduğu gibi bırakır', () => {
    expect(stateLabel('RUNNING')).toBe('Chaos uygulanıyor')
    expect(stateLabel('YENI_DURUM')).toBe('YENI_DURUM')
    expect(stateLabel(null)).toBe('—')
    expect(intensityLabel('MEDIUM')).toBe('Orta')
    expect(verdictLabel('IMPACT_CONFIRMED')).toBe('Etki doğrulandı')
    expect(verdictLabel(null)).toBe('—')
  })
  it('süreyi dakika ve saniye olarak yazar', () => {
    expect(humanDuration(45)).toBe('45 sn')
    expect(humanDuration(420)).toBe('7 dk')
    expect(humanDuration(450)).toBe('7 dk 30 sn')
    expect(humanDuration(null)).toBe('—')
  })
  it('parametre değerlerini JSON yerine düz metin gösterir', () => {
    expect(showValue(2500)).toBe('2500')
    expect(showValue(1.5)).toBe('1.5')
    expect(showValue(null)).toBe('—')
    expect(showValue({ holdMs: 2500, pool: 'main' })).toBe('Bağlantıyı tutma süresi (ms): 2500 · pool: main')
  })
})

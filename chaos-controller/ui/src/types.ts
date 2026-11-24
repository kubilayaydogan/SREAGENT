// Backend (/api/v2) veri tipleri — backend tek doğruluk kaynağıdır; UI yalnız gösterir.

export type State =
  | 'CREATED' | 'VALIDATING' | 'BASELINING' | 'RAMPING_UP' | 'RUNNING' | 'RECOVERING'
  | 'COMPLETED' | 'FAILED' | 'ABORTED' | 'TIMED_OUT'

export const TERMINAL: State[] = ['COMPLETED', 'FAILED', 'ABORTED', 'TIMED_OUT']
export const PHASES: State[] = ['CREATED', 'VALIDATING', 'BASELINING', 'RAMPING_UP', 'RUNNING', 'RECOVERING', 'COMPLETED']

export type Intensity = 'LIGHT' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | 'CUSTOM'
export type TargetMode = 'SINGLE' | 'MULTI' | 'ALL' | 'SHARED_DB' | 'RANDOM' | 'AUTO'

export interface AppSample {
  up: boolean
  cpuPct: number | null
  heapUsedMb: number | null
  heapMaxMb: number | null
  poolActive: number | null
  poolIdle: number | null
  poolPending: number | null
  poolMax: number | null
  rps: number | null
  errorPct: number | null
  p95Ms: number | null
  p99Ms: number | null
  dbConnections: number | null
  dbLockWaits: number | null
  heapLiveMb: number | null
}

export interface DbSample {
  up: boolean
  cpuPct: number | null
  memUsedPct: number | null
  load1: number | null
  cpus: number | null
  connections: number | null
  maxConnections: number | null
  active: number | null
  idleInTx: number | null
  lockWaiters: number | null
  longestXactSec: number | null
  tps: number | null
  deadlocksPerMin: number | null
  tempMbPerSec: number | null
  readMbPerSec: number | null
  writeMbPerSec: number | null
  insertsPerSec: number | null
  queryMeanMs: number | null
  dbSizeMb: number | null
}

export interface HostSample { cpuPct: number | null; memUsedPct: number | null; diskFreeGb: number | null }

export interface WorkloadSample {
  part: string
  workers: number
  callsPerSec: number
  errorPct: number
  p95Ms: number | null
  amplification: number
  controlValue: number | null
  controlTarget: number | null
}

export interface Sample {
  at: string
  tMs: number
  phase: State | null
  apps: Record<string, AppSample>
  db: DbSample | null
  appVm: HostSample | null
  workloads: WorkloadSample[]
  traffic: Record<string, unknown>
}

export interface AppCard {
  app: string
  sample: AppSample | null
  heapPct: number | null
  heapLivePct: number | null
  poolUsagePct: number | null
  activeChaos: { experimentId: string; scenario: string; roles: string[]; state: State } | null
}

export interface Summary {
  id: string
  createdAt: string
  scenarioId: string
  scenarioName: string
  initiator: string
  targets: string[]
  intensity: string
  state: State
  stateReason: string | null
  startedAt: string | null
  endedAt: string | null
  label: string | null
  randomPlanId: string | null
  verdict: string | null
  recovered: boolean | null
}

export interface Overview {
  apps: AppCard[]
  db: DbSample | null
  dbConnectionsPct: number | null
  dbError: string | null
  appVm: HostSample | null
  sampledAt: string | null
  active: Summary | null
  queue: Summary[]
  environment: string
  calibration: Record<string, unknown>
}

export interface Scenario {
  id: string
  name: string
  category: string
  description: string
  prerequisites: string[]
  initiatorRole: string
  directTarget: string
  minApps: number
  defaultAllTargets: boolean
  expectedImpact: string[]
  abortConditions: string[]
  cleanup: string
}

export interface Custom {
  workers?: number | null
  rps?: number | null
  dbConnections?: number | null
  txDurationMs?: number | null
  queryFrequency?: number | null
  targetValue?: number | null
  errorPct?: number | null
  networkLatencyMs?: number | null
  packetLossPct?: number | null
  maxAffectedServices?: number | null
  budget?: {
    maxDbCpuPct?: number | null
    maxDbConnectionsPct?: number | null
    maxAppCpuPct?: number | null
    maxAppHeapPct?: number | null
    maxDbMemPct?: number | null
  } | null
}

export interface Durations { baselineSec: number; rampSec: number; durationSec: number; recoverySec: number; maxDurationSec: number }

export interface Traffic {
  enabled: boolean
  apps: string[]
  rpsPerApp: number
  users: number
  readWriteRatio: number
  timeoutMs: number
  retries: number
  retryBackoffMs: number
  thinkTimeMs: number
  maxTotalRps: number
  hotRows: boolean
}

export interface ExperimentRequest {
  scenarioId: string
  initiator: string | null
  targetMode: TargetMode
  targets: string[]
  intensity: Intensity
  custom: Custom | null
  durations: Durations
  traffic: Traffic
  seed: number | null
  label: string | null
  clientRequestId: string | null
  randomPlanId?: string | null
  subScenarios?: string[] | null
  cooldownSec?: number | null
}

export interface Criterion {
  description: string
  metric: string
  metricLabel?: string
  scope: string
  agg: string
  expected: string
  required: boolean
  key?: string | null
  observed?: number | null
  baseline?: number | null
  threshold?: number | null
  result?: 'PASS' | 'FAIL' | 'NO_DATA'
}

export interface Experiment {
  id: string
  createdAt: string
  request: ExperimentRequest
  scenarioName: string
  initiator: string
  targets: string[]
  participants: string[]
  intensity: string
  seed: number
  state: State
  stateReason: string | null
  transitions: { state: State; at: string; monoMs: number; reason: string }[]
  logs: { at: string; level: string; message: string }[]
  plan: Record<string, unknown> & { criteria?: Criterion[]; expectedAffected?: string[] }
  samples: Sample[]
  workloadSummary: Record<string, unknown>[]
  report: (Record<string, unknown> & {
    verdict?: string
    criteria?: Criterion[]
    expectedVsObserved?: { expectedAffected: string[]; observedAffected: string[]; unexpectedlyAffected: string[]; expectedButNotObserved: string[] }
    recovery?: { recovered?: boolean; recoveryTimeSec?: number | null; lastChecks?: { check: string; value: unknown; pass: boolean }[] }
    controller?: Record<string, unknown>
  }) | null
  dynatrace: Record<string, string> | null
  startedAt: string | null
  endedAt: string | null
  cleanupOk: boolean
  cleanupErrors: string[]
}

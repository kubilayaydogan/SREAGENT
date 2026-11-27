// Ekranda gösterilen adlar ve kısa açıklamalar. Backend değerleri (durum, yoğunluk, sonuç kodları) değişmez;
// burada yalnız okurun göreceği karşılıkları tutulur. Bilinmeyen değer olduğu gibi gösterilir.
import type { Durations, Intensity, State, TargetMode } from './types'

export const STATE_LABEL: Record<State, string> = {
  CREATED: 'Sırada',
  VALIDATING: 'Ön kontrol',
  BASELINING: 'Baseline ölçümü',
  RAMPING_UP: 'Yük artırılıyor',
  RUNNING: 'Chaos uygulanıyor',
  RECOVERING: 'Toparlanma izleniyor',
  COMPLETED: 'Tamamlandı',
  FAILED: 'Başarısız',
  ABORTED: 'Durduruldu',
  TIMED_OUT: 'Süre sınırı aşıldı',
}

export const STATE_HELP: Record<State, string> = {
  CREATED: 'Deney kaydedildi, sırasını bekliyor.',
  VALIDATING: 'Uygulamalar ve PostgreSQL erişilebilir mi, güvenlik sınırları uygun mu kontrol ediliyor.',
  BASELINING: 'Chaos başlamadan önce sistemin olağan değerleri ölçülüyor. Sonuç bu değerlerle karşılaştırılır.',
  RAMPING_UP: 'Yük kademeli olarak hedef seviyeye çıkarılıyor.',
  RUNNING: 'Senaryo hedef seviyede çalışıyor. Etki bu aşamada ölçülür.',
  RECOVERING: 'Yük durduruldu, temizlik yapıldı. Sistemin baseline değerlerine dönüp dönmediği izleniyor.',
  COMPLETED: 'Deney bitti, rapor hazır.',
  FAILED: 'Deney bir hata nedeniyle tamamlanamadı.',
  ABORTED: 'Deney kullanıcı ya da bir güvenlik sınırı tarafından durduruldu.',
  TIMED_OUT: 'Deney izin verilen en uzun süreyi aştığı için sonlandırıldı.',
}

export function stateLabel(s: string | null | undefined): string {
  return (s && (STATE_LABEL as Record<string, string>)[s]) || s || '—'
}

export const INTENSITY_LABEL: Record<Intensity, string> = {
  LIGHT: 'Hafif', MEDIUM: 'Orta', HIGH: 'Yüksek', CRITICAL: 'Kritik', CUSTOM: 'Özel',
}
export const INTENSITY_HELP: Record<Intensity, string> = {
  LIGHT: 'Ölçülebilir ama küçük etki. İlk deneme için uygun.',
  MEDIUM: 'Belirgin yavaşlama; hata beklenmez.',
  HIGH: 'Ciddi yavaşlama, timeout ve hata görülebilir.',
  CRITICAL: 'Sınıra kadar zorlar; uygulama geçici olarak yanıt vermeyebilir.',
  CUSTOM: 'Değerleri kendiniz girersiniz. Boş bıraktığınız alanlar ortama göre ayarlanır.',
}
export function intensityLabel(i: string | null | undefined): string {
  return (i && (INTENSITY_LABEL as Record<string, string>)[i]) || i || '—'
}

export const TARGET_MODES: { v: TargetMode; label: string; help: string }[] = [
  { v: 'AUTO', label: 'Otomatik', help: 'Senaryonun önerdiği uygulamalar yüke katılır. Emin değilseniz bunu seçin.' },
  { v: 'SINGLE', label: 'Tek uygulama', help: 'Yükü, başlatan uygulamayla birlikte yalnız seçtiğiniz uygulama üretir.' },
  { v: 'MULTI', label: 'Seçtiklerim', help: 'İşaretlediğiniz her uygulama aynı veritabanı yükünü kendi bağlantılarıyla başlatır.' },
  { v: 'ALL', label: 'Tüm uygulamalar', help: 'Dört uygulamanın hepsi veritabanı yükünü birlikte başlatır.' },
  { v: 'SHARED_DB', label: 'Paylaşılan PostgreSQL', help: 'Dört uygulama da yükü başlatır; etki paylaşılan veritabanı üzerinden hepsine yansıyabilir.' },
  { v: 'RANDOM', label: 'Rastgele', help: 'Yüke katılan uygulamalar seed değerine göre rastgele seçilir.' },
]

export const VERDICT_LABEL: Record<string, string> = {
  IMPACT_CONFIRMED: 'Etki doğrulandı',
  IMPACT_NOT_OBSERVED: 'Etki gözlenmedi',
  INCONCLUSIVE: 'Sonuç belirsiz',
}
export const VERDICT_HELP: Record<string, string> = {
  IMPACT_CONFIRMED: 'Zorunlu ölçütlerin hepsi sağlandı: senaryo beklenen etkiyi üretti.',
  IMPACT_NOT_OBSERVED: 'Zorunlu ölçütlerden en az biri sağlanmadı: beklenen etki ölçülmedi.',
  INCONCLUSIVE: 'Karar vermek için yeterli ölçüm yok ya da deney yarıda kaldı.',
}
export function verdictLabel(v: string | null | undefined): string {
  return (v && VERDICT_LABEL[v]) || v || '—'
}

export const RESULT_LABEL: Record<string, string> = { PASS: 'Sağlandı', FAIL: 'Sağlanmadı', NO_DATA: 'Veri yok' }

export const ROLE_LABEL: Record<string, string> = {
  initiator: 'yükü başlatan',
  'direct target': 'doğrudan hedef',
  'observed affected': 'etkilendiği ölçüldü',
}

/** Backend rol adı ("INITIATOR", "direct target", "DIRECT_TARGET" …) → okunur karşılık. */
export function roleLabel(r: string): string {
  return ROLE_LABEL[r.toLowerCase().replace(/[_-]/g, ' ')] ?? (r === 'participant' ? 'katılımcı' : r)
}

export const CATEGORY_LABEL: Record<string, string> = {
  connection: 'Connection Pool ve bağlantı',
  lock: 'Lock ve transaction',
  'db-cpu': 'Veritabanı CPU',
  'db-io': 'Veritabanı I/O ve WAL',
  'db-memory': 'Veritabanı belleği',
  'db-maintenance': 'Vacuum ve dead tuple',
  combined: 'Birden fazla yük tipi',
  // eski deney kayıtlarındaki kategoriler (artık senaryo üretmez)
  'app-resource': 'Uygulama kaynakları (CPU, bellek)',
  network: 'Network',
  amplification: 'Retry ve yük çoğalması',
  cascade: 'Zincirleme etki',
  load: 'Yük',
}
export function categoryLabel(c: string): string {
  return CATEGORY_LABEL[c] ?? c
}

export const DURATION_FIELDS: { k: keyof Durations; label: string; help: string; tone?: string }[] = [
  { k: 'baselineSec', label: 'Baseline ölçümü', help: 'Chaos öncesi olağan değerlerin ölçüldüğü süre. SRE Agent karşılaştırması için en az 180 sn önerilir.', tone: 'base' },
  { k: 'rampSec', label: 'Yük artırma', help: 'Yükün hedef seviyeye çıkarıldığı süre.', tone: 'ramp' },
  { k: 'durationSec', label: 'Chaos', help: 'Senaryonun hedef seviyede çalıştığı süre.', tone: 'run' },
  { k: 'recoverySec', label: 'Toparlanma', help: 'Yük durduktan sonra sistemin izlendiği süre.', tone: 'rec' },
  { k: 'maxDurationSec', label: 'En uzun süre', help: 'Deney bu süreyi aşarsa sonlandırılır. 0 = ortamın varsayılan sınırı.' },
]

export const PHASE_TONE: Partial<Record<State, string>> = { BASELINING: 'base', RAMPING_UP: 'ramp', RUNNING: 'run', RECOVERING: 'rec' }

/** Saniyeyi "7 dk 30 sn" biçiminde yazar. */
export function humanDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec) || sec < 0) return '—'
  const s = Math.round(sec)
  const m = Math.floor(s / 60)
  const r = s % 60
  if (m === 0) return `${r} sn`
  return r === 0 ? `${m} dk` : `${m} dk ${r} sn`
}

/** Önizleme ve preset parametrelerinde anahtar adlarının okunur karşılıkları; bilinmeyen anahtar olduğu gibi kalır. */
export const PARAM_LABEL: Record<string, string> = {
  poolMax: 'Connection Pool boyutu',
  holdMs: 'Bağlantıyı tutma süresi (ms)',
  targetDemandPct: 'Hedef pool talebi (%)',
  maxWorkers: 'En fazla eşzamanlı worker',
  minWorkers: 'En az worker',
  workers: 'Worker sayısı',
  rps: 'İstek hızı (rps)',
  txMs: 'Transaction süresi (ms)',
  txDurationMs: 'Transaction süresi (ms)',
  delayMs: 'Eklenen network latency (ms)',
  lossPct: 'Packet loss (%)',
  targetCpuPct: 'Hedef CPU (%)',
  targetPct: 'Hedef seviye (%)',
  errorPct: 'Hata olasılığı (%)',
  retries: 'Retry sayısı',
  timeoutMs: 'Timeout (ms)',
  apps: 'Yükü başlatan uygulamalar',
  loadTypes: 'Birleştirilen yük tipleri',
  records: 'Paylaşılan satır sayısı',
  keys: 'Advisory lock anahtarı sayısı',
  pairs: 'Satır çifti sayısı',
  targetLockWaiters: 'Hedef lock bekleyen session',
  targetDbCpuPct: 'Hedef DB CPU (%)',
  targetInsertsPerSec: 'Hedef insert hızı (satır/sn)',
  targetUpdatesPerSec: 'Hedef update hızı (satır/sn)',
  targetTempMbPerSec: 'Hedef temp yazımı (MB/sn)',
  targetTps: 'Hedef transaction hızı (TPS)',
  targetRollbacksPerSec: 'Hedef rollback hızı (/sn)',
  workersPerQuery: 'Sorgu başına parallel worker',
  targetIdleSessions: 'Hedef idle in transaction session',
  rowsPerCall: 'Çağrı başına satır',
  commitsPerCall: 'Çağrı başına commit',
  queriesPerCall: 'Çağrı başına sorgu',
  sessionsPerCall: 'Çağrı başına session',
  connectionsPerCall: 'Çağrı başına bağlantı',
  achievablePct: 'Ulaşılabilir doluluk (%)',
  snapshotHoldMs: 'Snapshot tutma süresi (ms)',
  sortMb: 'Sort başına bellek (MB)',
  workersPerApp: 'Uygulama başına worker',
  estimatedPeakMb: 'Tahmini en yüksek bellek (MB)',
  subScenarios: 'Seçilen senaryolar',
}
export function paramLabel(k: string): string {
  return PARAM_LABEL[k] ?? k
}

/** Düz bir değeri okunur metne çevirir (nesne ise "anahtar: değer" listesi). */
export function showValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/\.?0+$/, '')
  if (typeof v === 'boolean') return v ? 'evet' : 'hayır'
  if (Array.isArray(v)) return v.map(showValue).join(', ') || '—'
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${paramLabel(k)}: ${showValue(x)}`).join(' · ')
  }
  return String(v)
}

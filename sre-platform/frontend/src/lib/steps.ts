/** Agent Activity adımları — backend sre_platform/agent/events.py STEPS ile aynı sıra. */
export const STEPS: { id: string; label: string; hint: string }[] = [
  { id: "start", label: "Araştırma başladı", hint: "pencere ve baseline belirlenir" },
  { id: "app_metrics", label: "Uygulama metrikleri", hint: "HTTP, Hikari havuzu, JVM" },
  { id: "db_metrics", label: "PostgreSQL metrikleri", hint: "bağlantı, oturum, kilit bekleyen" },
  { id: "logs", label: "Hata / kilit logları", hint: "kilit kenarları, PG logları" },
  { id: "traces", label: "Dağıtık izler", hint: "span gecikmeleri, yavaş trace'ler" },
  { id: "infra", label: "Host / container", hint: "CPU, bellek, container" },
  { id: "extra", label: "Ek kanıt", hint: "mekanizma doğrulama" },
  { id: "correlate", label: "Kanıt ilişkilendirme", hint: "mekanizma ve risk puanı" },
  { id: "memory", label: "Geçmiş kayıtlar", hint: "benzer araştırma, incident, runbook" },
  { id: "rca", label: "RCA / yanıt", hint: "özet ve öneriler" },
  { id: "save", label: "Kaydetme", hint: "PostgreSQL" },
];

export const SIGNAL_LABEL: Record<string, string> = {
  http: "HTTP istekleri",
  hikari: "Hikari bağlantı havuzu",
  jvm_gc: "JVM GC",
  jvm_heap: "JVM heap",
  db_conn: "PG bağlantı oranı",
  db_sessions: "PG oturumları",
  lock_waiters: "Kilit bekleyenler",
  collector: "PG collector",
  db_stats: "PG istatistikleri",
  statements: "SQL ifadeleri",
  edges: "Kilit kenarları",
  pg_lock_logs: "PG kilit logları",
  spans: "Span gecikmeleri",
  hosts: "Host CPU/bellek",
  containers: "Container kaynakları",
  edges_detail: "Kilit kenarı detayı",
  top_statements: "En yavaş SQL",
  slow_traces: "En yavaş istekler (trace)",
  problems: "Davis problemleri",
};

export const MECHANISM_LABEL: Record<string, string> = {
  row_lock: "Satır kilidi",
  pool_saturation: "Bağlantı havuzu doygunluğu",
  global_connection_pressure: "Global PG bağlantı baskısı",
  db_cpu: "DB CPU baskısı",
  app_vm_cpu: "APP_VM CPU baskısı",
  independent_load: "Bağımsız yük",
};

export function mechanismLabel(id: string | null | undefined): string {
  if (!id) return "—";
  return MECHANISM_LABEL[id] ?? id;
}

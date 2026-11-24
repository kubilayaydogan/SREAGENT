/** Agent Activity adımları — backend sre_platform/agent/events.py STEPS ile aynı sıra. */
export const STEPS: { id: string; label: string; hint: string }[] = [
  { id: "start", label: "Araştırma başladı", hint: "pencere ve baseline belirlenir" },
  { id: "app_metrics", label: "Uygulama metric'leri", hint: "HTTP, Hikari Connection Pool, JVM" },
  { id: "db_metrics", label: "PostgreSQL metric'leri", hint: "connection, session, Lock Wait" },
  { id: "logs", label: "Error / lock logları", hint: "blocking edge'ler, PG logları" },
  { id: "traces", label: "Distributed Tracing", hint: "span latency, yavaş trace'ler" },
  { id: "infra", label: "Host / container", hint: "CPU, bellek, container" },
  { id: "extra", label: "Ek evidence", hint: "mechanism doğrulama" },
  { id: "correlate", label: "Evidence correlation", hint: "mechanism ve risk score" },
  { id: "memory", label: "Geçmiş kayıtlar", hint: "benzer araştırma, incident, runbook" },
  { id: "rca", label: "RCA / yanıt", hint: "özet ve öneriler" },
  { id: "save", label: "Kaydetme", hint: "PostgreSQL" },
];

export const SIGNAL_LABEL: Record<string, string> = {
  http: "HTTP istekleri",
  hikari: "Hikari Connection Pool",
  jvm_gc: "JVM GC",
  jvm_heap: "JVM heap",
  db_conn: "PG bağlantı oranı",
  db_sessions: "PG oturumları",
  lock_waiters: "Lock Wait'teki session'lar",
  collector: "PG collector",
  db_stats: "PG istatistikleri",
  statements: "SQL ifadeleri",
  edges: "Blocking edge'ler",
  pg_lock_logs: "PG lock logları",
  spans: "Span latency",
  hosts: "Host CPU/bellek",
  containers: "Container kaynakları",
  edges_detail: "Blocking edge detayı",
  top_statements: "En yavaş SQL",
  slow_traces: "En yavaş istekler (trace)",
  problems: "Davis problemleri",
};

export const MECHANISM_LABEL: Record<string, string> = {
  row_lock: "Row lock wait",
  pool_saturation: "Connection Pool saturation",
  global_connection_pressure: "PostgreSQL global connection pressure",
  db_cpu: "DB_VM CPU pressure",
  app_vm_cpu: "APP_VM CPU pressure",
  app_container_cpu: "Container CPU saturation",
  network_path_latency: "App ↔ PostgreSQL network path latency",
  independent_load: "Bağımsız load artışı",
};

export function mechanismLabel(id: string | null | undefined): string {
  if (!id) return "—";
  return MECHANISM_LABEL[id] ?? id;
}

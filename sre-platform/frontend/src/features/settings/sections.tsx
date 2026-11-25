import { FlaskConical, Play, Send, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "@/services/api";
import { useAuth } from "@/services/auth";
import { Button, Card, CardBody, CardHeader, Field, Input, Select } from "@/components/ui/primitives";
import { ErrorState, Notice, TableSkeleton } from "@/components/ui/states";
import { cn, fmtNum, fmtRelative, fmtTime } from "@/lib/utils";
import {
  useJobs,
  useMemoryStatus,
  useRetentionPreview,
  useSecrets,
  useSettingsAction,
  type RiskFixtureRow,
  type SectionView,
  type TestResult,
} from "./api";
import { ConfirmDialog } from "./common";
import { SectionForm, type Draft, type FieldRenderer } from "./SectionForm";

function TestButton({ path, label, icon = "test" }: { path: string; label: string; icon?: "test" | "send" }) {
  const [res, setRes] = useState<TestResult | null>(null);
  const m = useSettingsAction<void, TestResult>(() => api.post<TestResult>(path), "Test başarısız");
  const { can } = useAuth();
  if (!can("admin")) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" loading={m.isPending} onClick={() => m.mutate(undefined, { onSuccess: setRes })}>
        {icon === "send" ? <Send /> : <FlaskConical />} {label}
      </Button>
      {res && (
        <span className={cn("text-xs", res.ok ? "text-st-good-ink" : "text-st-critical-ink")} role="status">
          {res.ok ? "Başarılı" : "Başarısız"}: {String(res.detail ?? res.message ?? "")}
        </span>
      )}
    </div>
  );
}

function StatusCard({ title, rows }: { title: string; rows: [string, ReactNode][] }) {
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody>
        <dl>
          {rows.map(([k, v]) => (
            <Field key={k} label={k}>
              {v}
            </Field>
          ))}
        </dl>
      </CardBody>
    </Card>
  );
}

// ------------------------------------------------------------------ General
export function GeneralSection() {
  const render: FieldRenderer = ({ name, value, onChange, disabled }) =>
    name === "timezone" ? (
      <div className="flex items-center gap-2">
        <Input className="w-64" list="tz-list" value={String(value ?? "")} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
        <datalist id="tz-list">
          {["browser", "Europe/Istanbul", "UTC", "Europe/London", "Europe/Berlin", "America/New_York"].map((z) => (
            <option key={z} value={z} />
          ))}
        </datalist>
      </div>
    ) : undefined;
  return (
    <SectionForm
      section="general"
      title="General & Appearance"
      description="Platform geneli görüntü tercihleri. Tema (açık/koyu) kullanıcıya özeldir ve üst bardan seçilir; tarayıcıda saklanır."
      render={render}
    />
  );
}

// ------------------------------------------------------------------ Agent
export function AgentSection() {
  return (
    <SectionForm
      section="agent"
      title="Agent"
      description="Investigation çalışma zamanı. Her koşu başında bu değerler dondurulur (config snapshot); koşu sürerken yapılan değişiklik o koşuyu etkilemez."
      aside={(v) => {
        const st = v.status as { llm_available: boolean; llm_reason: string | null; graph: string[]; backoff: string };
        return (
          <StatusCard
            title="Çalışma zamanı"
            rows={[
              ["LLM", st.llm_available ? "kullanılabilir" : st.llm_reason ?? "yok"],
              ["Graph adımları", st.graph.join(" → ")],
              ["MCP retry backoff", st.backoff],
            ]}
          />
        );
      }}
    />
  );
}

// ------------------------------------------------------------------ Scheduling
const JOB_LABEL: Record<string, string> = {
  "health-check": "Health check (tam analiz)",
  watch: "Watch",
  "langsmith-sync": "LangSmith senkronu",
  retention: "Retention temizliği",
};

function JobsTable() {
  const q = useJobs();
  const { can } = useAuth();
  const run = useSettingsAction<string>((n) => api.post(`/settings/scheduling/jobs/${n}/run`), "Çalıştırılamadı", () => "Kuyruğa alındı; worker ≤5 sn içinde başlatır");
  return (
    <Card>
      <CardHeader title="Zamanlanmış işler" description="Durum worker'ın iş tablosundan okunur (canlı)." />
      {q.isLoading ? (
        <TableSkeleton rows={4} cols={5} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>İş</th>
                <th>Durum</th>
                <th>Aralık</th>
                <th>Sonraki çalışmalar</th>
                <th>Son koşu</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {q.data!.items.map((j) => (
                <tr key={j.name}>
                  <td>{JOB_LABEL[j.name] ?? j.name}</td>
                  <td>{j.running ? `çalışıyor (${j.locked_by})` : j.enabled ? "açık" : "kapalı"}</td>
                  <td>{j.interval_seconds >= 60 ? `${fmtNum(j.interval_seconds / 60)} dk` : `${j.interval_seconds} sn`}</td>
                  <td className="text-xs">{j.next_runs.length ? j.next_runs.map((t) => fmtTime(t, false)).join(" · ") : "—"}</td>
                  <td className="text-xs">
                    {j.last_run ? (
                      <span title={j.last_run.error ?? undefined} className={j.last_run.status === "FAILED" ? "text-st-critical-ink" : undefined}>
                        {j.last_run.status} · {fmtRelative(j.last_run.started_at)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="text-right">
                    {can("admin") && (
                      <Button size="sm" variant="ghost" disabled={!j.enabled || j.running} loading={run.isPending && run.variables === j.name} onClick={() => run.mutate(j.name)}>
                        <Play /> Şimdi çalıştır
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function SchedulingSection() {
  return (
    <SectionForm
      section="scheduling"
      title="Scheduling"
      description="Aralık (interval) tabanlı zamanlama. Kaçırılan periyotlar biriktirilmez (coalesce); bir iş çalışırken ikinci koşu başlamaz (çoklu worker'da da)."
      aside={(v) => {
        const cron = (v.status as { cron: { reason: string } }).cron;
        return <Notice tone="info">Cron ifadesi desteklenmiyor: {cron.reason}.</Notice>;
      }}
    >
      {() => <JobsTable />}
    </SectionForm>
  );
}

// ------------------------------------------------------------------ Risk
const COMP_LABEL: Record<string, string> = { latency: "Latency", errors: "Errors", saturation: "Saturation", lock: "Lock", resource: "Resource" };
const RAMP_LABEL: Record<string, string> = {
  latency_ratio: "Latency oranı (× baseline)",
  error_rate: "Error rate (oran)",
  pool_ratio: "Connection Pool doluluğu (oran)",
  lock_waiters: "Lock waiter (ortalama)",
  cpu_pct: "CPU (%)",
};

function NumberCell({ value, onChange, disabled, step = "any", label }: { value: number; onChange: (v: number) => void; disabled: boolean; step?: string; label: string }) {
  return <Input type="number" step={step} aria-label={label} className="w-24" value={Number.isFinite(value) ? value : ""} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />;
}

const riskRender: FieldRenderer = ({ name, value, onChange, disabled }) => {
  if (name === "weights") {
    const w = value as Record<string, number>;
    const sum = Object.values(w).reduce((a, b) => a + (Number(b) || 0), 0);
    return (
      <div className="space-y-1">
        {Object.entries(w).map(([k, v]) => (
          <div key={k} className="flex items-center gap-2">
            <span className="w-24 text-xs text-ink-2">{COMP_LABEL[k] ?? k}</span>
            <NumberCell label={`ağırlık ${k}`} value={v} disabled={disabled} onChange={(x) => onChange({ ...w, [k]: x })} />
          </div>
        ))}
        <div className={cn("text-xs", Math.abs(sum - 1) > 1e-6 ? "text-st-critical-ink" : "text-muted")}>Toplam {sum.toFixed(3)} (1.000 olmalı)</div>
      </div>
    );
  }
  if (name === "ramps") {
    const r = value as Record<string, [number, number]>;
    return (
      <div className="space-y-1">
        {Object.entries(r).map(([k, [lo, hi]]) => (
          <div key={k} className="flex flex-wrap items-center gap-2">
            <span className="w-48 text-xs text-ink-2">{RAMP_LABEL[k] ?? k}</span>
            <NumberCell label={`${k} alt`} value={lo} disabled={disabled} onChange={(x) => onChange({ ...r, [k]: [x, hi] })} />
            <span className="text-xs text-muted">→</span>
            <NumberCell label={`${k} üst`} value={hi} disabled={disabled} onChange={(x) => onChange({ ...r, [k]: [lo, x] })} />
          </div>
        ))}
        <div className="text-2xs text-muted">Alt değerin altı 0, üst değerin üstü 1; arası doğrusal.</div>
      </div>
    );
  }
  if (name === "bands") {
    const b = value as Record<string, number>;
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
        {(["low", "medium", "high"] as const).map((k) => (
          <label key={k} className="flex items-center gap-1">
            {{ low: "düşük <", medium: "orta <", high: "yüksek <" }[k]}
            <NumberCell label={`bant ${k}`} value={b[k]} disabled={disabled} step="1" onChange={(x) => onChange({ ...b, [k]: x })} />
          </label>
        ))}
        <span className="text-muted">≥ high: kritik</span>
      </div>
    );
  }
  return undefined;
};

function RiskPreview({ draft }: { draft: Draft }) {
  const [rows, setRows] = useState<RiskFixtureRow[] | null>(null);
  const m = useSettingsAction<Draft, { fixtures: RiskFixtureRow[] }>((d) => api.post("/settings/risk/preview", d), "Önizleme hesaplanamadı");
  const sc = (v: { score: number | null; band: string | null }) => (v.score === null ? "hesaplanamadı" : `${fmtNum(v.score, 1)} (${v.band})`);
  return (
    <Card>
      <CardHeader
        title="Önizleme"
        description="Sabit test senaryoları, mevcut deterministik puanlayıcıyla geçerli ve taslak ayarlarla puanlanır. Geçmiş skorlar yeniden hesaplanmaz."
        actions={
          <Button size="sm" loading={m.isPending} onClick={() => m.mutate(draft, { onSuccess: (r) => setRows(r.fixtures) })}>
            <FlaskConical /> Taslağı önizle
          </Button>
        }
      />
      {rows && (
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Senaryo</th>
                <th>Geçerli</th>
                <th>Taslak</th>
                <th>Fark</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <div className="text-[13px] text-ink">{r.title}</div>
                    <div className="text-2xs text-muted">{r.note}</div>
                  </td>
                  <td>{sc(r.current)}</td>
                  <td>{sc(r.draft)}</td>
                  <td>{r.delta === null ? "—" : `${r.delta > 0 ? "+" : ""}${fmtNum(r.delta, 1)}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function RiskSection() {
  return (
    <SectionForm
      section="risk"
      title="Risk Scoring"
      description="Deterministik risk score (0–100) bileşen ağırlıkları, normalizasyon aralıkları ve bantlar. Eksik veri 0 sayılmaz; coverage altında skor 'hesaplanamadı' olur."
      render={riskRender}
    >
      {(draft) => <RiskPreview draft={draft} />}
    </SectionForm>
  );
}

// ------------------------------------------------------------------ Observability
export function ObservabilitySection() {
  return (
    <SectionForm
      section="observability"
      title="Observability"
      description="LangSmith tracing. Yalnız izinli metadata ve adım özetleri gönderilir; soru, prompt, model çıktısı, ham DQL, MCP verisi ve hata ayrıntısı gönderilmez. Test ve production için ayrı project adı kullanın. API key ortamda (LANGSMITH_API_KEY) tutulur."
      aside={(v: SectionView) => {
        const st = v.status as { langsmith_key: boolean; tracer_active: boolean; restart_required: boolean; opentelemetry: { reason: string } };
        return (
          <>
            <StatusCard
              title="Durum"
              rows={[
                ["LANGSMITH_API_KEY", st.langsmith_key ? "tanımlı" : "tanımlı değil"],
                ["Tracer (bu süreç)", st.tracer_active ? "aktif" : "kapalı"],
                ["OpenTelemetry", `desteklenmiyor — ${st.opentelemetry.reason}`],
              ]}
            />
            {st.restart_required && <Notice tone="warning">Tracing açıldı ancak bu süreçte tracer yok; backend/worker yeniden başlatılınca etkinleşir.</Notice>}
            <TestButton path="/settings/observability/test" label="LangSmith bağlantısını test et" />
          </>
        );
      }}
    />
  );
}

// ------------------------------------------------------------------ Notifications
export function NotificationsSection() {
  const sec = useSecrets();
  const refs = [...(sec.data?.stored ?? []).map((s) => s.ref), ...(sec.data?.env ?? []).map((e) => e.ref ?? "")].filter(Boolean);
  const render: FieldRenderer = ({ name, value, onChange, disabled }) =>
    name.endsWith("_ref") ? (
      <Select className="w-72" value={String(value ?? "")} disabled={disabled} onChange={(e) => onChange(e.target.value || null)} aria-label={`${name} secret`}>
        <option value="">— seçilmedi —</option>
        {Array.from(new Set([...(value ? [String(value)] : []), ...refs])).map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </Select>
    ) : undefined;
  return (
    <SectionForm
      section="notifications"
      title="Notifications"
      description="Webhook adresleri secret olarak tutulur (Security & Governance › Secrets); burada yalnız referansı seçilir. Investigation başına en fazla bir RCA bildirimi. SIEM iletimi ve güvenlik alarmları varsayılan kapalıdır; durumları Security & Governance ekranındadır."
      render={render}
      aside={(v) => {
        const st = v.status as { channels: { id: string; label: string; supported: boolean; reason?: string }[]; retries: string };
        return (
          <>
            <StatusCard
              title="Kanallar"
              rows={[
                ...st.channels.map((c) => [c.label, c.supported ? "destekleniyor" : `desteklenmiyor (${c.reason})`] as [string, string]),
                ["Retry", st.retries],
              ]}
            />
            <TestButton path="/settings/notifications/test" label="Test bildirimi gönder" icon="send" />
          </>
        );
      }}
    />
  );
}

// ------------------------------------------------------------------ Retention & Memory
const fmtBytes = (b: number) => (b > 1 << 20 ? `${fmtNum(b / (1 << 20), 1)} MB` : `${fmtNum(b / 1024, 0)} KB`);

function MemoryPanel() {
  const q = useMemoryStatus();
  const { can } = useAuth();
  const [thread, setThread] = useState("");
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const del = useSettingsAction<string, { deleted_checkpoints: number }>(
    (t) => api.post(`/settings/memory/threads/${encodeURIComponent(t)}/delete`, { confirm: t }),
    "Checkpoint silinemedi",
    (r) => `${r.deleted_checkpoints} checkpoint silindi`,
  );
  if (q.isLoading) return <TableSkeleton rows={3} cols={2} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const m = q.data;
  return (
    <Card>
      <CardHeader title="Memory / Persistence" description={m.note} />
      <CardBody className="space-y-3">
        <dl className="grid gap-x-6 md:grid-cols-2">
          <div>
            <Field label="Checkpointer">{m.checkpointer.backend} (kalıcı)</Field>
            <Field label="Kapsam">{m.checkpointer.scope}</Field>
            <Field label="Thread / checkpoint">
              {fmtNum(m.checkpointer.threads)} / {fmtNum(m.checkpointer.checkpoints)} · {fmtBytes(m.checkpointer.bytes)}
            </Field>
          </div>
          <div>
            <Field label="Store">{m.store.backend} (kalıcı)</Field>
            <Field label="Kapsam">{m.store.scope}</Field>
            <Field label="Kayıt">
              {fmtNum(m.store.items)} · {fmtBytes(m.store.bytes)}
            </Field>
          </div>
          <div>
            <Field label="Episodic memory">{m.episodes.table}</Field>
            <Field label="Kapsam">{m.episodes.scope}</Field>
            <Field label="Episode">
              {fmtNum(m.episodes.total)} · confirmed {fmtNum(m.episodes.by_outcome.confirmed)} · partially_confirmed{" "}
              {fmtNum(m.episodes.by_outcome.partially_confirmed)} · rejected {fmtNum(m.episodes.by_outcome.rejected)} · unverified{" "}
              {fmtNum(m.episodes.by_outcome.unverified)}
            </Field>
            <Field label="Çözüm kayıtlı">
              {fmtNum(m.episodes.with_resolution)} · retention: {m.episodes.retention}
            </Field>
          </div>
          <div>
            <Field label="Working memory">checkpointer (konuşma thread'i)</Field>
            <Field label="Kapsam">{m.working_memory.scope}</Field>
            <Field label="Sınırlar">
              son {m.working_memory.max_messages} mesaj · {m.working_memory.max_digests} tur özeti
            </Field>
          </div>
        </dl>
        <table className="data-table w-full">
          <thead>
            <tr>
              <th>Backend</th>
              <th>Durum</th>
              <th>Not</th>
            </tr>
          </thead>
          <tbody>
            {m.supported_backends.map((b) => (
              <tr key={b.id}>
                <td>{b.label}</td>
                <td>{b.status}</td>
                <td className="text-xs text-muted">{b.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Notice tone="info">{m.switching} Checkpoint retention: {m.retention.checkpoints}.</Notice>
        {can("admin") && (
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label htmlFor="thread-id" className="mb-1 block text-xs font-medium text-ink-2">
                Thread checkpoint'lerini sil (investigation run_id veya konuşma thread_id)
              </label>
              <Input id="thread-id" className="w-80 font-mono" value={thread} onChange={(e) => setThread(e.target.value.trim())} />
            </div>
            <Button size="sm" variant="danger" disabled={!thread} onClick={() => setOpen(true)}>
              <Trash2 /> Sil…
            </Button>
          </div>
        )}
      </CardBody>
      <ConfirmDialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          setConfirm("");
        }}
        title="Checkpoint silme"
        danger
        confirmLabel="Kalıcı olarak sil"
        busy={del.isPending}
        disabled={confirm !== thread}
        onConfirm={() => del.mutate(thread, { onSuccess: () => setOpen(false) })}
      >
        <p>
          <code className="font-mono">{thread}</code> thread'inin tüm checkpoint'leri kalıcı olarak silinir; yarıda kalan tur/koşu artık sürdürülemez.
          Investigation kaydı ve raporu silinmez. Aktif koşunun checkpoint'leri silinemez.
        </p>
        <label className="block">
          Onaylamak için thread kimliğini yazın:
          <Input className="mt-1 font-mono" value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-label="onay" />
        </label>
      </ConfirmDialog>
    </Card>
  );
}

export const RETENTION_LABEL: Record<string, string> = {
  investigations: "Investigation",
  watch_events: "Watch olayı",
  job_runs: "İş çalıştırma kaydı",
  llm_runs: "LLM Monitoring özeti",
  llm_usage: "LLM kullanım kaydı",
  auth_login_failures: "Başarısız giriş kaydı",
  checkpoint_threads_orphan: "Sahipsiz checkpoint thread'i",
  checkpoint_threads_ttl: "Süresi dolan checkpoint thread'i",
  store_items: "Store öğesi",
  trace_links: "Sohbet turu trace eşlemesi",
};

function RetentionPreviewCard() {
  const q = useRetentionPreview();
  if (!q.data) return null;
  const rows = Object.entries(q.data.counts).map(([k, n]) => [RETENTION_LABEL[k] ?? k, String(n)] as [string, string]);
  return (
    <StatusCard
      title="Bir sonraki turda silinecekler (önizleme, kaydedilmiş ayarlarla)"
      rows={[...rows, ["Audit kayıtları", q.data.audit], ["Zamanlama", q.data.schedule]]}
    />
  );
}

export function RetentionSection() {
  return (
    <SectionForm
      section="retention"
      title="Retention & Memory"
      description="Saklama süreleri saatlik retention işi tarafından uygulanır. Incident'a bağlı, RCA'lı, feedback'li ya da sohbette yer alan investigation'lar silinmez. Sahibi silinmiş checkpoint'ler 1 gün sonra temizlenir; checkpoint/Store yaş TTL'leri varsayılan kapalıdır. Her silme turu audit'e özetlenir; audit kayıtları silinmez."
      aside={() => <RetentionPreviewCard />}
    >
      {() => <MemoryPanel />}
    </SectionForm>
  );
}


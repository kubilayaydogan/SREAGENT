import { useQueryClient } from "@tanstack/react-query";
import { Play, Square } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ActivityTimeline, toolsFromRecords } from "@/components/ActivityTimeline";
import { InvestigationStatusBadge, SeverityBadge } from "@/components/badges";
import { AppSelect } from "@/components/filters";
import { ToolTable } from "@/components/RecordTables";
import { JobsCard } from "@/features/JobsCard";
import { Button, Card, CardBody, CardHeader, Input, Label, Select, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Page, PageHeader } from "@/components/ui/states";
import { MetricStrip, StatTile } from "@/components/StatTile";
import { errorToast, useInvestigation, useInvestigations, useTools } from "@/hooks/queries";
import { useAgentRun } from "@/hooks/useAgentRun";
import { api } from "@/services/api";
import { useAuth } from "@/services/auth";
import { mechanismLabel, STEPS } from "@/lib/steps";
import { cn, fmtDuration, fmtRelative, fmtTime } from "@/lib/utils";
import type { RunOut, ToolExecution } from "@/types/api";

function ManualInvestigation() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [app, setApp] = useState("");
  const [minutes, setMinutes] = useState("15");
  const [title, setTitle] = useState("");
  const [useLlm, setUseLlm] = useState(true);
  const run = useAgentRun((e) => {
    if (e.type === "done") qc.invalidateQueries({ queryKey: ["investigations"] });
  });
  const busy = run.state.phase === "streaming";

  async function start() {
    try {
      const r = await api.post<RunOut>("/agent/investigate", {
        title: title.trim() || null,
        application_id: app || null,
        time_range: { minutes: Number(minutes) },
        use_llm: useLlm,
      });
      void run.attach(r.run_id, r.stream_url);
    } catch (e) {
      errorToast(e, "Investigation başlatılamadı");
    }
  }

  return (
    <Card>
      <CardHeader title="Manuel investigation" description="Sohbet olmadan tam telemetri analizi · adımlar canlı izlenir" />
      <CardBody className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label>Servis</Label>
            <AppSelect value={app} onChange={setApp} className="w-full" />
          </div>
          <div>
            <Label htmlFor="mi-win">Pencere</Label>
            <Select id="mi-win" value={minutes} onChange={(e) => setMinutes(e.target.value)}>
              {[
                ["15", "Son 15 dk"],
                ["30", "Son 30 dk"],
                ["60", "Son 1 saat"],
                ["180", "Son 3 saat"],
                ["360", "Son 6 saat"],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div>
          <Label htmlFor="mi-title">Başlık (isteğe bağlı)</Label>
          <Input id="mi-title" value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} placeholder="örn. Check-in latency 14:20" />
        </div>
        <label className="flex items-center gap-2 text-xs text-ink-2">
          <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} className="accent-accent" />
          Claude ile açıklama üret (kapalıysa deterministik özet)
        </label>
        <div className="flex gap-2">
          {busy ? (
            <Button size="sm" variant="outline" onClick={run.cancel}>
              <Square /> Durdur
            </Button>
          ) : (
            <Button size="sm" variant="primary" onClick={start}>
              <Play /> Başlat
            </Button>
          )}
          {run.state.investigationId && run.state.phase !== "streaming" && (
            <Button size="sm" variant="ghost" onClick={() => nav(`/investigations/${run.state.investigationId}`)}>
              Sonucu aç →
            </Button>
          )}
        </div>
        {run.state.phase !== "idle" && (
          <div className="rounded-md border border-line bg-surface-2 p-3">
            <div className="mb-2 text-xs text-muted" aria-live="polite">
              {run.state.thinking[run.state.thinking.length - 1]?.message ?? "Başlatılıyor…"}
              {run.state.connection === "reconnecting" && <span className="ml-2 text-st-warning-ink">yeniden bağlanılıyor…</span>}
            </div>
            <ActivityTimeline steps={run.state.steps} tools={run.state.tools} />
            {run.state.error && <p className="mt-2 text-xs text-st-critical-ink">{run.state.error.message}</p>}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/** Adım özeti: agent bu adımda ne yaptı, kaç çağrı yaptı, ne döndü, hangi evidence oluştu. Tool execution
 *  kayıtlarından üretilir; kaydı olmayan adım listede yer almaz. */
function StepSummary({ rows, onEvidence }: { rows: ToolExecution[]; onEvidence: (key: string) => void }) {
  const groups = STEPS.map((st) => ({ st, items: rows.filter((r) => r.step === st.id) })).filter((g) => g.items.length);
  if (!groups.length) return <EmptyState title="Bu çalıştırmada adım kaydı yok" description="Çalıştırma veri toplama adımına ulaşmadan bitmiş olabilir." />;
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Adım</th>
            <th>Agent ne yaptı?</th>
            <th className="num">Çağrı</th>
            <th>Ne döndü?</th>
            <th>Oluşan evidence</th>
          </tr>
        </thead>
        <tbody>
          {groups.map(({ st, items }) => {
            const failed = items.filter((r) => r.execution_status === "FAILED");
            const records = items.reduce((a, r) => a + (r.record_count ?? 0), 0);
            const counted = items.some((r) => r.record_count != null);
            const ev = [...new Set(items.map((r) => r.evidence_key).filter(Boolean) as string[])];
            return (
              <tr key={st.id}>
                <td className="text-xs whitespace-nowrap text-ink">{st.label}</td>
                <td className="text-xs text-ink-2">{st.hint}</td>
                <td className="num">{items.length}</td>
                <td className="text-xs">
                  {counted ? `${records} kayıt` : <span className="text-muted">kayıt sayısı yok</span>}
                  {failed.length > 0 && (
                    <span className="ml-1.5 text-st-critical-ink" title={failed.map((r) => r.error_message).filter(Boolean).join(" · ")}>
                      {failed.length} çağrı başarısız: bu veri sonuca girmedi
                    </span>
                  )}
                </td>
                <td className="font-mono text-2xs">
                  {ev.length
                    ? ev.slice(0, 6).map((k) => (
                        <button key={k} type="button" className="mr-2 text-accent-text hover:underline" onClick={() => onEvidence(k)}>
                          {k}
                        </button>
                      ))
                    : <span className="font-sans text-xs text-muted">—</span>}
                  {ev.length > 6 && <span className="text-muted">+{ev.length - 6}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function InvestigationActivity({ id }: { id: string }) {
  const inv = useInvestigation(id);
  const nav = useNavigate();
  const { can } = useAuth();
  const openEvidence = (k: string) => nav(`/investigations/${id}?tab=evidence&ev=${encodeURIComponent(k)}`);
  const live = inv.data?.status === "RUNNING" || inv.data?.status === "PENDING";
  const tools = useTools(id, live);
  if (inv.error) return <ErrorState error={inv.error} onRetry={() => inv.refetch()} />;
  const { tools: items, steps } = toolsFromRecords(tools.data ?? []);
  if (inv.data && !live) {
    steps.save = "done";
    if (inv.data.status === "COMPLETED" || inv.data.status === "PARTIAL") {
      steps.rca = "done";
      steps.correlate = steps.correlate ?? "done";
    }
    if (inv.data.status === "FAILED") steps.rca = "failed";
  }
  const totalMs = (tools.data ?? []).reduce((a, t) => a + (t.duration_ms ?? 0), 0);
  const failedN = (tools.data ?? []).filter((t) => t.execution_status === "FAILED").length;
  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title={inv.data?.title ?? "…"}
          description={
            inv.data && (
              <>
                {fmtTime(inv.data.window_start)} → {fmtTime(inv.data.window_end, false)} · {inv.data.trigger} · toplam {fmtDuration(inv.data.duration_ms)}
              </>
            )
          }
          actions={
            inv.data && (
              <>
                <SeverityBadge severity={inv.data.severity} />
                <InvestigationStatusBadge status={inv.data.status} />
                <Link to={`/investigations/${id}`} className="text-xs text-muted hover:text-ink">
                  detay →
                </Link>
              </>
            )
          }
        />
        <CardBody className="grid gap-5 lg:grid-cols-[22rem_1fr]">
          <div>
            {tools.isLoading ? <Skeleton className="h-64" /> : <ActivityTimeline steps={steps} tools={items} />}
          </div>
          <div className="space-y-2 text-[13px]">
            <MetricStrip className="grid-cols-3">
              <StatTile label="DQL query" value={tools.data?.length ?? "—"} />
              <StatTile
                label="Başarısız"
                value={failedN}
                tone={failedN ? "text-st-critical-ink" : undefined}
              />
              <StatTile label="Toplam query süresi" value={fmtDuration(totalMs)} />
            </MetricStrip>
            {inv.data?.primary_mechanism && (
              <p className="text-xs text-ink-2">
                Olası mechanism: <b className="font-medium text-ink">{mechanismLabel(inv.data.primary_mechanism)}</b>
              </p>
            )}
            {inv.data?.error && (
              <details className="text-xs">
                <summary className={cn("cursor-pointer", inv.data.status === "COMPLETED" || inv.data.status === "PARTIAL" ? "text-ink-2" : "text-st-critical-ink")}>
                  {inv.data.status === "COMPLETED" || inv.data.status === "PARTIAL" ? "Bir adım beklendiği gibi çalışmadı; sonuç eldeki veriyle üretildi." : "Çalıştırma tamamlanamadı."} Teknik ayrıntı
                </summary>
                <p className="mt-1 font-mono break-words text-ink-2">{inv.data.error}</p>
              </details>
            )}
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader
          title="Adım adım: agent ne yaptı?"
          description="Her satır bu çalıştırmanın kayıtlarından üretilir. Evidence kimliğine tıklayınca rapordaki evidence açılır."
          actions={
            can("sre") && inv.data ? (
              <Link to={`/agent-trace/${encodeURIComponent(inv.data.run_id)}`} className="text-xs text-accent-text hover:underline" title="Her node'un girdi / çıktı state'i ve değişen alanlar">
                State değişikliklerini aç (Agent Trace)
              </Link>
            ) : undefined
          }
        />
        {tools.isLoading ? <Skeleton className="m-3 h-40" /> : <StepSummary rows={tools.data ?? []} onEvidence={openEvidence} />}
      </Card>
      <Card>
        <CardHeader title="Tool executions" description="Her satır bir Dynatrace MCP çağrısı (execute-dql) veya iç adım" />
        {tools.isLoading ? <Skeleton className="m-3 h-40" /> : <ToolTable rows={tools.data ?? []} runId={can("sre") ? inv.data?.run_id : undefined} onEvidence={openEvidence} />}
      </Card>
    </div>
  );
}

export default function ActivityPage() {
  const { investigationId } = useParams();
  const { can } = useAuth();
  const recent = useInvestigations({ size: 15 });
  return (
    <Page>
      <PageHeader title="Agent Activity" description="Agent'ın çalışma adımları: tool, durum, süre, error ve evidence referansları" />
      <div className="grid items-start gap-3 xl:grid-cols-[20rem_1fr]">
        <div className="space-y-3">
          <Card>
            <CardHeader title="Son investigation'lar" />
            <ul className="max-h-[480px] divide-y divide-line overflow-y-auto">
              {recent.data?.items.map((i) => (
                <li key={i.id}>
                  <Link
                    to={`/activity/${i.id}`}
                    aria-current={i.id === investigationId ? "page" : undefined}
                    className={cn("block border-l-2 px-3 py-2 hover:bg-surface-2", i.id === investigationId ? "border-accent bg-surface-2" : "border-transparent")}
                  >
                    <div className="truncate text-[13px] text-ink">{i.title}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-2xs text-muted">
                      <InvestigationStatusBadge status={i.status} /> {fmtRelative(i.created_at)}
                    </div>
                  </Link>
                </li>
              ))}
              {recent.data && !recent.data.items.length && <EmptyState title="Henüz investigation yok" />}
            </ul>
          </Card>
          {can("sre") && <ManualInvestigation />}
          <JobsCard />
        </div>
        <div>
          {investigationId ? (
            <InvestigationActivity id={investigationId} />
          ) : (
            <Card>
              <EmptyState title="Bir investigation seçin" description="Soldan bir investigation seçin veya manuel investigation başlatın. Sohbetteki canlı adımlar AI Investigation ekranında da görünür." />
            </Card>
          )}
        </div>
      </div>
    </Page>
  );
}

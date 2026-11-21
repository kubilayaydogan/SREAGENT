import { useQueryClient } from "@tanstack/react-query";
import { Play, Square } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ActivityTimeline, toolsFromRecords } from "@/components/ActivityTimeline";
import { InvestigationStatusBadge, SeverityBadge } from "@/components/badges";
import { AppSelect } from "@/components/filters";
import { ToolTable } from "@/components/RecordTables";
import { Button, Card, CardBody, CardHeader, Input, Label, Select, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, PageHeader } from "@/components/ui/states";
import { errorToast, useInvestigation, useInvestigations, useTools } from "@/hooks/queries";
import { useAgentRun } from "@/hooks/useAgentRun";
import { api } from "@/services/api";
import { useAuth } from "@/services/auth";
import { mechanismLabel } from "@/lib/steps";
import { cn, fmtDuration, fmtRelative, fmtTime } from "@/lib/utils";
import type { RunOut } from "@/types/api";

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
      errorToast(e, "Araştırma başlatılamadı");
    }
  }

  return (
    <Card>
      <CardHeader title="Manuel araştırma" description="Sohbet olmadan tam telemetri analizi; adımlar canlı izlenir" />
      <CardBody className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label>Uygulama</Label>
            <AppSelect value={app} onChange={setApp} className="w-full" />
          </div>
          <div>
            <Label>Pencere</Label>
            <Select value={minutes} onChange={(e) => setMinutes(e.target.value)}>
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
          <Label>Başlık (isteğe bağlı)</Label>
          <Input value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} placeholder="örn. Check-in gecikmesi 14:20" />
        </div>
        <label className="flex items-center gap-2 text-xs text-ink-2">
          <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} className="accent-[#3987e5]" />
          Claude ile açıklama üret (kapalıysa deterministik özet)
        </label>
        <div className="flex gap-2">
          {busy ? (
            <Button variant="outline" onClick={run.cancel}>
              <Square /> Durdur
            </Button>
          ) : (
            <Button variant="primary" onClick={start}>
              <Play /> Başlat
            </Button>
          )}
          {run.state.investigationId && run.state.phase !== "streaming" && (
            <Button variant="ghost" onClick={() => nav(`/investigations/${run.state.investigationId}`)}>
              Sonucu aç →
            </Button>
          )}
        </div>
        {run.state.phase !== "idle" && (
          <div className="rounded-md border border-line bg-page/40 p-3">
            <div className="mb-2 text-xs text-muted" aria-live="polite">
              {run.state.thinking[run.state.thinking.length - 1]?.message ?? "Başlatılıyor…"}
              {run.state.connection === "reconnecting" && <span className="ml-2 text-st-warning">yeniden bağlanılıyor…</span>}
            </div>
            <ActivityTimeline steps={run.state.steps} tools={run.state.tools} />
            {run.state.error && <p className="mt-2 text-xs text-[#f08a8a]">{run.state.error.message}</p>}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function InvestigationActivity({ id }: { id: string }) {
  const inv = useInvestigation(id);
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
  return (
    <div className="space-y-4">
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
        <CardBody className="grid gap-6 lg:grid-cols-[340px_1fr]">
          <div>
            {tools.isLoading ? <Skeleton className="h-64" /> : <ActivityTimeline steps={steps} tools={items} />}
          </div>
          <div className="space-y-2 text-sm">
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="rounded-md border border-line p-2">
                <div className="text-muted">Sorgu</div>
                <div className="text-lg text-ink tabular-nums">{tools.data?.length ?? "—"}</div>
              </div>
              <div className="rounded-md border border-line p-2">
                <div className="text-muted">Başarısız</div>
                <div className={cn("text-lg tabular-nums", (tools.data ?? []).some((t) => t.execution_status === "FAILED") ? "text-st-critical" : "text-ink")}>
                  {(tools.data ?? []).filter((t) => t.execution_status === "FAILED").length}
                </div>
              </div>
              <div className="rounded-md border border-line p-2">
                <div className="text-muted">Toplam sorgu süresi</div>
                <div className="text-lg text-ink tabular-nums">{fmtDuration(totalMs)}</div>
              </div>
            </div>
            {inv.data?.primary_mechanism && (
              <p className="text-xs text-ink-2">
                Olası mekanizma: <b className="text-ink">{mechanismLabel(inv.data.primary_mechanism)}</b>
              </p>
            )}
            {inv.data?.error && <p className="font-mono text-xs text-[#f08a8a]">{inv.data.error}</p>}
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Araç çalıştırma geçmişi" description="Her satır bir Dynatrace MCP çağrısı (execute-dql) veya iç adım" />
        {tools.isLoading ? <Skeleton className="m-4 h-40" /> : <ToolTable rows={tools.data ?? []} />}
      </Card>
    </div>
  );
}

export default function ActivityPage() {
  const { investigationId } = useParams();
  const { can } = useAuth();
  const recent = useInvestigations({ size: 15 });
  return (
    <div className="mx-auto max-w-[1500px] p-4 lg:p-6">
      <PageHeader title="Agent Activity" description="Ajanın çalışma adımları: araç, durum, süre, hata ve kanıt referansları" />
      <div className="grid gap-4 xl:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader title="Son araştırmalar" />
            <ul className="max-h-[480px] divide-y divide-line overflow-y-auto">
              {recent.data?.items.map((i) => (
                <li key={i.id}>
                  <Link to={`/activity/${i.id}`} className={cn("block px-4 py-2 hover:bg-surface-2", i.id === investigationId && "bg-surface-3")}>
                    <div className="truncate text-sm text-ink">{i.title}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted">
                      <InvestigationStatusBadge status={i.status} /> {fmtRelative(i.created_at)}
                    </div>
                  </Link>
                </li>
              ))}
              {recent.data && !recent.data.items.length && <EmptyState title="Henüz araştırma yok" />}
            </ul>
          </Card>
          {can("sre") && <ManualInvestigation />}
        </div>
        <div>
          {investigationId ? (
            <InvestigationActivity id={investigationId} />
          ) : (
            <Card>
              <EmptyState title="Bir araştırma seçin" description="Soldan bir araştırma seçin veya manuel araştırma başlatın. Sohbetteki canlı adımlar AI Investigation ekranında da görünür." />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

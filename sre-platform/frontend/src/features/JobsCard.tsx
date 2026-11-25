import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { Card, CardHeader, Skeleton } from "@/components/ui/primitives";
import { EmptyState } from "@/components/ui/states";
import { errorToast } from "@/hooks/queries";
import { api } from "@/services/api";
import { useAuth } from "@/services/auth";
import { cn, fmtDuration, fmtRelative } from "@/lib/utils";
import type { Job, JobRun } from "@/types/api";

const JOB_TR: Record<string, string> = { watch: "Watch (sürekli monitoring)", health_check: "Health check", retention: "Retention / cleanup" };

/** Zamanlanmış işler (sre-worker). Kiralama sayesinde bir iş aynı anda tek worker'da çalışır. */
export function JobsCard() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api.get<Job[]>("/jobs"), refetchInterval: 30_000 });
  const runs = useQuery({ queryKey: ["job-runs"], queryFn: () => api.get<JobRun[]>("/jobs/runs?limit=40"), refetchInterval: 30_000 });
  const toggle = useMutation({
    mutationFn: (j: Job) => api.post(`/jobs/${encodeURIComponent(j.name)}/enabled?enabled=${!j.enabled}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
    onError: (e) => errorToast(e),
  });
  const lastOf = (id: string) => runs.data?.find((r) => r.job_id === id);
  return (
    <Card>
      <CardHeader title="Zamanlanmış kontroller" description="sre-worker · ayarlar ortam değişkenlerinden; worker yeniden başlayınca uygulanır" />
      {jobs.isLoading ? (
        <Skeleton className="m-4 h-24" />
      ) : !jobs.data?.length ? (
        <EmptyState title="Zamanlanmış iş yok" description="sre-worker henüz çalışmadı." />
      ) : (
        <ul className="divide-y divide-line">
          {jobs.data.map((j) => {
            const r = lastOf(j.id);
            const invId = r?.result?.investigation_id as string | undefined;
            const open = r?.result?.open_conditions as string[] | undefined;
            return (
              <li key={j.id} className="px-3 py-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] text-ink">{JOB_TR[j.job_type] ?? j.name}</span>
                  <span className="text-muted">her {j.interval_seconds % 60 === 0 ? `${j.interval_seconds / 60} dk` : fmtDuration(j.interval_seconds * 1000)}</span>
                  {can("admin") ? (
                    <button
                      onClick={() => toggle.mutate(j)}
                      aria-pressed={j.enabled} className={cn("ml-auto rounded-sm border border-line-strong px-1.5 py-0.5", j.enabled ? "text-ink" : "text-muted")}
                    >
                      {j.enabled ? "açık" : "kapalı"}
                    </button>
                  ) : (
                    <span className={cn("ml-auto", j.enabled ? "text-ink-2" : "text-muted")}>{j.enabled ? "açık" : "kapalı"}</span>
                  )}
                </div>
                <div className="mt-0.5 text-muted">
                  {r ? (
                    <>
                      son: {fmtRelative(r.finished_at ?? r.started_at)} ·{" "}
                      <span className={r.status === "FAILED" ? "text-st-critical-ink" : r.status === "RUNNING" ? "text-accent-text" : "text-ink-2"}>
                        {r.status === "SUCCEEDED" ? "başarılı" : r.status === "FAILED" ? "başarısız" : "çalışıyor"}
                      </span>
                      {invId && (
                        <>
                          {" · "}
                          <Link to={`/investigations/${invId}`} className="hover:text-ink">
                            investigation →
                          </Link>
                        </>
                      )}
                      {open && open.length > 0 && <span className="text-st-warning-ink"> · açık koşul: {open.join(", ")}</span>}
                      {r.error && <div className="font-mono text-2xs break-all text-st-critical-ink">{r.error}</div>}
                    </>
                  ) : (
                    "henüz çalışmadı"
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

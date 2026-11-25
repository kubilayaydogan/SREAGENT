import { Ban, Check, CheckCircle2, Clock, X, XCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { StatusLabel } from "@/components/badges";
import { FilterBar } from "@/components/table";
import { Button, Card, Segmented, Textarea } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Page, PageHeader, TableSkeleton } from "@/components/ui/states";
import { useApprovalMutations, useApprovals } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { cn, fmtRelative, fmtTime } from "@/lib/utils";
import type { Approval } from "@/types/api";

const ST: Record<Approval["status"], { label: string; tone: "warning" | "good" | "critical" | "unknown"; icon: ReactNode }> = {
  PENDING: { label: "Bekliyor", tone: "warning", icon: <Clock /> },
  APPROVED: { label: "Onaylandı", tone: "good", icon: <CheckCircle2 /> },
  REJECTED: { label: "Reddedildi", tone: "critical", icon: <XCircle /> },
  EXPIRED: { label: "Süresi doldu", tone: "unknown", icon: <Clock /> },
  CANCELLED: { label: "İptal", tone: "unknown", icon: <Ban /> },
};

export default function ApprovalsPage() {
  const { user, can } = useAuth();
  const [status, setStatus] = useState("PENDING");
  const { data, isLoading, error, refetch } = useApprovals(status || undefined);
  const { decide, cancel } = useApprovalMutations();
  const [dlg, setDlg] = useState<{ a: Approval; approve: boolean } | null>(null);
  const [comment, setComment] = useState("");

  return (
    <Page>
      <PageHeader
        title="Onaylar"
        description="Human-in-the-loop · agent salt-okunurdur; restart, rollback, DB ayarı, SQL yazma gibi aksiyonlar talep edenden farklı bir yöneticinin onayıyla elle uygulanır"
      />
      <Card>
        <FilterBar right={data && <span className="text-xs text-muted tabular-nums">{data.total} talep</span>}>
          <Segmented
            label="durum"
            value={status}
            onChange={setStatus}
            options={[
              { value: "PENDING", label: "Bekleyen" },
              { value: "APPROVED", label: "Onaylanan" },
              { value: "REJECTED", label: "Reddedilen" },
              { value: "", label: "Tümü" },
            ]}
          />
        </FilterBar>
        {isLoading ? (
          <TableSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="Onay talebi yok" description="Talepler incident ekranındaki 'Onay iste' ile oluşturulur." />
        ) : (
          <div className="overflow-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Aksiyon</th>
                  <th>Gerekçe</th>
                  <th>Durum</th>
                  <th>Talep</th>
                  <th>Karar</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => {
                  const own = a.requested_by === user?.id;
                  return (
                    <tr key={a.id} className="[&>td]:align-top">
                      <td>
                        <div className="font-mono text-xs text-ink">{a.action_type}</div>
                        <div className="text-xs text-ink-2">{a.target}</div>
                        {a.incident_id && (
                          <Link to={`/incidents/${a.incident_id}`} className="text-2xs text-muted hover:text-ink">
                            incident →
                          </Link>
                        )}
                      </td>
                      <td className="max-w-md text-xs whitespace-pre-wrap">{a.justification}</td>
                      <td>
                        <StatusLabel tone={ST[a.status].tone} icon={ST[a.status].icon}>
                          {ST[a.status].label}
                        </StatusLabel>
                      </td>
                      <td className="text-xs whitespace-nowrap text-muted">
                        {fmtTime(a.created_at)}
                        {own && <div>(sizin talebiniz)</div>}
                        {a.expires_at && a.status === "PENDING" && <div>son: {fmtRelative(a.expires_at).replace("önce", "sonra")}</div>}
                      </td>
                      <td className="text-xs text-muted">
                        {a.decided_at ? fmtTime(a.decided_at) : "—"}
                        {a.decision_comment && <div className="text-ink-2">{a.decision_comment}</div>}
                      </td>
                      <td>
                        {a.status === "PENDING" && (
                          <div className="flex justify-end gap-1">
                            {can("admin") && !own && (
                              <>
                                <Button size="sm" variant="primary" onClick={() => (setComment(""), setDlg({ a, approve: true }))}>
                                  <Check /> Onayla
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => (setComment(""), setDlg({ a, approve: false }))}>
                                  <X /> Reddet
                                </Button>
                              </>
                            )}
                            {own && (
                              <Button size="sm" variant="ghost" loading={cancel.isPending} onClick={() => cancel.mutate(a.id)}>
                                İptal et
                              </Button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {dlg && (
        <Dialog
          open
          onOpenChange={(v) => !v && setDlg(null)}
          title={dlg.approve ? "Aksiyonu onayla" : "Aksiyonu reddet"}
          description={`${dlg.a.action_type} → ${dlg.a.target}. Onay, aksiyonu otomatik uygulamaz; uygulayan kişi kayda geçer.`}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDlg(null)}>
                Vazgeç
              </Button>
              <Button
                variant={dlg.approve ? "primary" : "danger"}
                loading={decide.isPending}
                onClick={() => decide.mutate({ id: dlg.a.id, approve: dlg.approve, comment: comment.trim() || undefined }, { onSuccess: () => setDlg(null) })}
              >
                {dlg.approve ? "Onayla" : "Reddet"}
              </Button>
            </>
          }
        >
          <Textarea aria-label="yorum" rows={3} value={comment} maxLength={4000} onChange={(e) => setComment(e.target.value)} placeholder="Yorum (isteğe bağlı)" className={cn("w-full")} />
        </Dialog>
      )}
    </Page>
  );
}

import { Check, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { Badge, Button, Card, Select, Textarea } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, PageHeader, TableSkeleton } from "@/components/ui/states";
import { useApprovalMutations, useApprovals } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { cn, fmtRelative, fmtTime } from "@/lib/utils";
import type { Approval } from "@/types/api";

const ST_TR: Record<Approval["status"], [string, string]> = {
  PENDING: ["Bekliyor", "border-st-warning/50 text-st-warning"],
  APPROVED: ["Onaylandı", "border-st-good/50 text-st-good"],
  REJECTED: ["Reddedildi", "border-st-critical/50 text-[#f08a8a]"],
  EXPIRED: ["Süresi doldu", "text-muted"],
  CANCELLED: ["İptal", "text-muted"],
};

export default function ApprovalsPage() {
  const { user, can } = useAuth();
  const [status, setStatus] = useState("PENDING");
  const { data, isLoading, error, refetch } = useApprovals(status || undefined);
  const { decide, cancel } = useApprovalMutations();
  const [dlg, setDlg] = useState<{ a: Approval; approve: boolean } | null>(null);
  const [comment, setComment] = useState("");

  return (
    <div className="mx-auto max-w-[1500px] p-4 lg:p-6">
      <PageHeader
        title="Onaylar (human-in-the-loop)"
        description="Ajan salt-okunurdur. Yeniden başlatma, rollback, DB ayarı, SQL yazma gibi aksiyonlar talep edenden farklı bir yöneticinin onayıyla, elle uygulanır."
        actions={
          <Select aria-label="durum" value={status} onChange={(e) => setStatus(e.target.value)} className="w-40">
            <option value="PENDING">Bekleyenler</option>
            <option value="APPROVED">Onaylananlar</option>
            <option value="REJECTED">Reddedilenler</option>
            <option value="">Tümü</option>
          </Select>
        }
      />
      <Card>
        {isLoading ? (
          <TableSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="Onay talebi yok" description="Talepler incident ekranındaki 'Onay iste' ile oluşturulur." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-medium">Aksiyon</th>
                  <th className="px-4 py-2 font-medium">Gerekçe</th>
                  <th className="px-4 py-2 font-medium">Durum</th>
                  <th className="px-4 py-2 font-medium">Talep</th>
                  <th className="px-4 py-2 font-medium">Karar</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => {
                  const own = a.requested_by === user?.id;
                  return (
                    <tr key={a.id} className="border-b border-line align-top last:border-0">
                      <td className="px-4 py-2">
                        <div className="font-mono text-xs text-ink">{a.action_type}</div>
                        <div className="text-xs text-ink-2">{a.target}</div>
                        {a.incident_id && (
                          <Link to={`/incidents/${a.incident_id}`} className="text-[11px] text-muted hover:text-ink">
                            incident →
                          </Link>
                        )}
                      </td>
                      <td className="max-w-md px-4 py-2 text-xs whitespace-pre-wrap text-ink-2">{a.justification}</td>
                      <td className="px-4 py-2">
                        <Badge className={ST_TR[a.status][1]}>{ST_TR[a.status][0]}</Badge>
                      </td>
                      <td className="px-4 py-2 text-xs text-muted">
                        {fmtTime(a.created_at)}
                        {own && <div>(sizin talebiniz)</div>}
                        {a.expires_at && a.status === "PENDING" && <div>son: {fmtRelative(a.expires_at).replace("önce", "sonra")}</div>}
                      </td>
                      <td className="px-4 py-2 text-xs text-muted">
                        {a.decided_at ? fmtTime(a.decided_at) : "—"}
                        {a.decision_comment && <div className="text-ink-2">{a.decision_comment}</div>}
                      </td>
                      <td className="px-4 py-2">
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
          <Textarea rows={3} value={comment} maxLength={4000} onChange={(e) => setComment(e.target.value)} placeholder="Yorum (isteğe bağlı)" className={cn("w-full")} />
        </Dialog>
      )}
    </div>
  );
}

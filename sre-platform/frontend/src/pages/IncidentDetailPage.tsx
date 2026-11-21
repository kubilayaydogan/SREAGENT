import { ClipboardCheck, FileText, Link2, Pencil, Plus, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { IncidentStatusBadge, INC_STATUS_LABEL, InvestigationStatusBadge, SeverityBadge, SEVERITY_LABEL } from "@/components/badges";
import { Markdown } from "@/components/Markdown";
import { Button, Card, CardBody, CardHeader, Input, Label, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useApprovalMutations, useGenerateRca, useIncident, useIncidentMutations } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { mechanismLabel } from "@/lib/steps";
import { fmtRelative, fmtTime } from "@/lib/utils";
import type { IncidentDetail, IncidentStatus, Severity } from "@/types/api";

const TRANSITION_VERB: Record<IncidentStatus, string> = {
  OPEN: "Yeniden aç",
  INVESTIGATING: "İncelemeye al",
  MITIGATED: "Hafifletildi",
  RESOLVED: "Çözüldü",
};
const ACTIONS = ["RESTART", "ROLLBACK", "DB_CONFIG", "SQL_WRITE", "INFRA", "CONTAINER_STOP"];
const KIND_TR: Record<string, string> = { status: "durum", note: "not", investigation: "araştırma bağlandı", update: "güncelleme", rca: "RCA" };

function Timeline({ inc }: { inc: IncidentDetail }) {
  return (
    <ol className="relative space-y-3 pl-5">
      <span aria-hidden className="absolute top-1 bottom-1 left-[5px] w-px bg-line-strong" />
      {[...inc.timeline].reverse().map((e) => (
        <li key={e.id} className="relative text-sm">
          <span aria-hidden className="absolute top-1.5 -left-5 size-2.5 rounded-full border-2 border-surface bg-line-strong" />
          <div className="text-xs text-muted">
            {fmtTime(e.at)} · {KIND_TR[e.kind] ?? e.kind}
          </div>
          {e.to_status && (
            <div className="text-ink">
              {e.from_status ? `${INC_STATUS_LABEL[e.from_status as IncidentStatus] ?? e.from_status} → ` : ""}
              {INC_STATUS_LABEL[e.to_status as IncidentStatus] ?? e.to_status}
            </div>
          )}
          {e.message && <div className="whitespace-pre-wrap text-ink-2">{e.message}</div>}
        </li>
      ))}
    </ol>
  );
}

function EditDialog({ inc, open, onOpenChange }: { inc: IncidentDetail; open: boolean; onOpenChange: (v: boolean) => void }) {
  const { patch } = useIncidentMutations(inc.id);
  const [f, setF] = useState({
    title: inc.title,
    severity: inc.severity,
    description: inc.description ?? "",
    root_cause: inc.root_cause ?? "",
    actions: inc.recommended_actions.join("\n"),
  });
  return (
    <Dialog
      wide
      open={open}
      onOpenChange={onOpenChange}
      title="Incident'ı düzenle"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Vazgeç
          </Button>
          <Button
            variant="primary"
            loading={patch.isPending}
            onClick={() =>
              patch.mutate(
                {
                  title: f.title.trim(),
                  severity: f.severity,
                  description: f.description.trim() || null,
                  root_cause: f.root_cause.trim() || null,
                  recommended_actions: f.actions.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 30),
                },
                { onSuccess: () => onOpenChange(false) },
              )
            }
          >
            Kaydet
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-[1fr_160px] gap-3">
          <div>
            <Label>Başlık</Label>
            <Input value={f.title} maxLength={300} onChange={(e) => setF({ ...f, title: e.target.value })} />
          </div>
          <div>
            <Label>Önem</Label>
            <Select value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value as Severity })}>
              {(Object.keys(SEVERITY_LABEL) as Severity[]).map((s) => (
                <option key={s} value={s}>
                  {SEVERITY_LABEL[s]}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div>
          <Label>Açıklama</Label>
          <Textarea rows={3} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        </div>
        <div>
          <Label>Kök neden</Label>
          <Textarea rows={3} value={f.root_cause} onChange={(e) => setF({ ...f, root_cause: e.target.value })} />
        </div>
        <div>
          <Label>Önerilen aksiyonlar (satır başına bir)</Label>
          <Textarea rows={4} value={f.actions} onChange={(e) => setF({ ...f, actions: e.target.value })} />
        </div>
      </div>
    </Dialog>
  );
}

function ApprovalDialog({ inc, open, onOpenChange }: { inc: IncidentDetail; open: boolean; onOpenChange: (v: boolean) => void }) {
  const { request } = useApprovalMutations();
  const [f, setF] = useState({ action_type: "RESTART", target: inc.application_id ?? "", justification: "" });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Aksiyon için onay iste"
      description="Ajan hiçbir değişiklik yapmaz. Kritik aksiyonlar bir yöneticinin (talep edenden farklı kişi) onayından sonra elle uygulanır."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Vazgeç
          </Button>
          <Button
            variant="primary"
            disabled={f.justification.trim().length < 10 || !f.target.trim()}
            loading={request.isPending}
            onClick={() => request.mutate({ ...f, target: f.target.trim(), justification: f.justification.trim(), incident_id: inc.id, investigation_id: inc.investigation_id }, { onSuccess: () => onOpenChange(false) })}
          >
            Talep oluştur
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Aksiyon</Label>
            <Select value={f.action_type} onChange={(e) => setF({ ...f, action_type: e.target.value })}>
              {ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Hedef</Label>
            <Input value={f.target} maxLength={200} onChange={(e) => setF({ ...f, target: e.target.value })} />
          </div>
        </div>
        <div>
          <Label>Gerekçe (en az 10 karakter)</Label>
          <Textarea rows={4} value={f.justification} onChange={(e) => setF({ ...f, justification: e.target.value })} />
        </div>
      </div>
    </Dialog>
  );
}

export default function IncidentDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const { data: inc, isLoading, error, refetch } = useIncident(id);
  const { patch, link } = useIncidentMutations(id);
  const gen = useGenerateRca();
  const [note, setNote] = useState("");
  const [linkId, setLinkId] = useState("");
  const [editing, setEditing] = useState(false);
  const [approving, setApproving] = useState(false);
  const sre = can("sre");

  if (error) return <ErrorState className="mt-10" error={error} onRetry={() => refetch()} />;
  if (isLoading || !inc)
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-64" />
      </div>
    );
  const trigger = inc.investigations.find((i) => i.relation === "trigger") ?? inc.investigations[0];

  return (
    <div className="mx-auto max-w-[1500px] p-4 lg:p-6">
      <Link to="/incidents" className="text-xs text-muted hover:text-ink">
        ← Incident'lar
      </Link>
      <div className="mt-1 mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-ink">{inc.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <IncidentStatusBadge status={inc.status} />
            <SeverityBadge severity={inc.severity} />
            <span>{inc.application_id ?? "uygulama —"}</span>
            <span>· kaynak {inc.source}</span>
            <span>· tespit {fmtTime(inc.detected_at)}</span>
            {inc.resolved_at && <span>· çözüm {fmtTime(inc.resolved_at)}</span>}
          </div>
        </div>
        {sre && (
          <div className="flex flex-wrap gap-2">
            {inc.allowed_transitions.map((s) => (
              <Button key={s} size="sm" variant={s === "RESOLVED" ? "primary" : "outline"} loading={patch.isPending && patch.variables?.status === s} onClick={() => patch.mutate({ status: s })}>
                {TRANSITION_VERB[s]}
              </Button>
            ))}
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              <Pencil /> Düzenle
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setApproving(true)}>
              <ClipboardCheck /> Onay iste
            </Button>
          </div>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <CardHeader title="Açıklama ve kök neden" />
            <CardBody className="space-y-3 text-sm">
              {inc.description ? <Markdown>{inc.description}</Markdown> : <p className="text-muted">Açıklama yok.</p>}
              <div>
                <div className="mb-1 text-xs font-medium text-ink">Kök neden</div>
                <p className={inc.root_cause ? "text-ink-2" : "text-muted"}>{inc.root_cause ?? "Henüz belirlenmedi."}</p>
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-ink">Önerilen aksiyonlar</div>
                {inc.recommended_actions.length ? (
                  <ul className="list-disc space-y-0.5 pl-5 text-ink-2">
                    {inc.recommended_actions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted">—</p>
                )}
              </div>
              {inc.conditions.length > 0 && (
                <div className="text-xs text-muted">Watch koşulları: {inc.conditions.join(", ")}</div>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader
              title="İlgili araştırmalar"
              actions={
                sre && (
                  <form
                    className="flex gap-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const v = linkId.trim().replace(/^.*investigations\//, "");
                      if (v) link.mutate({ investigation_id: v, relation: "related" }, { onSuccess: () => setLinkId("") });
                    }}
                  >
                    <Input value={linkId} onChange={(e) => setLinkId(e.target.value)} placeholder="araştırma ID / bağlantı" className="h-7 w-56 text-xs" />
                    <Button size="sm" variant="outline" type="submit" loading={link.isPending}>
                      <Link2 /> Bağla
                    </Button>
                  </form>
                )
              }
            />
            {inc.investigations.length ? (
              <ul className="divide-y divide-line">
                {inc.investigations.map((i) => (
                  <li key={i.id} className="flex items-center gap-3 px-4 py-2.5">
                    <div className="min-w-0 flex-1">
                      <Link to={`/investigations/${i.id}`} className="block truncate text-sm text-ink hover:underline">
                        {i.title}
                      </Link>
                      <div className="text-xs text-muted">
                        {i.relation} · {mechanismLabel(i.primary_mechanism)} · {fmtRelative(i.created_at)}
                      </div>
                    </div>
                    <SeverityBadge severity={i.severity} />
                    <InvestigationStatusBadge status={i.status} />
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="Bağlı araştırma yok" />
            )}
          </Card>
          <Card>
            <CardHeader
              title="RCA raporları"
              actions={
                sre &&
                trigger &&
                (trigger.status === "COMPLETED" || trigger.status === "PARTIAL") && (
                  <Button size="sm" variant="outline" loading={gen.isPending} onClick={() => gen.mutate({ investigationId: trigger.id, incident_id: inc.id }, { onSuccess: (r) => nav(`/rca/${r.id}`) })}>
                    <FileText /> RCA üret
                  </Button>
                )
              }
            />
            {inc.rca_reports.length ? (
              <ul className="divide-y divide-line">
                {inc.rca_reports.map((r) => (
                  <li key={r.id}>
                    <Link to={`/rca/${r.id}`} className="block px-4 py-2.5 hover:bg-surface-2">
                      <div className="text-sm text-ink">{r.title}</div>
                      <div className="text-xs text-muted">
                        {fmtTime(r.generated_at)} · {r.generated_by}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="RCA raporu yok" />
            )}
          </Card>
        </div>
        <Card className="h-fit">
          <CardHeader title="Zaman çizelgesi" />
          <CardBody className="space-y-4">
            {sre && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (note.trim()) patch.mutate({ note: note.trim() }, { onSuccess: () => setNote("") });
                }}
                className="space-y-2"
              >
                <Textarea rows={2} value={note} maxLength={4000} onChange={(e) => setNote(e.target.value)} placeholder="Not ekle…" />
                <div className="flex justify-end gap-1">
                  {note && (
                    <Button size="sm" variant="ghost" type="button" onClick={() => setNote("")}>
                      <X />
                    </Button>
                  )}
                  <Button size="sm" type="submit" disabled={!note.trim()} loading={patch.isPending && !!patch.variables?.note}>
                    <Plus /> Not
                  </Button>
                </div>
              </form>
            )}
            <Timeline inc={inc} />
          </CardBody>
        </Card>
      </div>
      {editing && <EditDialog inc={inc} open={editing} onOpenChange={setEditing} />}
      {approving && <ApprovalDialog inc={inc} open={approving} onOpenChange={setApproving} />}
    </div>
  );
}

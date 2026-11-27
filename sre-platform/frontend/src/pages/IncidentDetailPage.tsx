import { ClipboardCheck, FileText, Link2, Pencil, Plus, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { AppTag, IncidentStatusBadge, INC_STATUS_LABEL, InvestigationStatusBadge, SeverityBadge, SEVERITY_LABEL } from "@/components/badges";
import { RiskValue } from "@/components/risk";
import { Markdown } from "@/components/Markdown";
import { Button, Card, CardBody, CardHeader, Input, Label, SectionTitle, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { ErrorState, Page, PageHeader } from "@/components/ui/states";
import { toNum } from "@/lib/risk";
import { useApprovalMutations, useGenerateRca, useIncident, useIncidentMutations } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { mechanismLabel } from "@/lib/steps";
import { fmtDuration, fmtRelative, fmtTime, shortId } from "@/lib/utils";
import type { IncidentDetail, IncidentStatus, Severity } from "@/types/api";

const TRANSITION_VERB: Record<IncidentStatus, string> = {
  OPEN: "Yeniden aç",
  INVESTIGATING: "İncelemeye al",
  MITIGATED: "Hafifletildi",
  RESOLVED: "Çözüldü",
};
const ACTIONS = ["RESTART", "ROLLBACK", "DB_CONFIG", "SQL_WRITE", "INFRA", "CONTAINER_STOP"];
const KIND_TR: Record<string, string> = { status: "durum", note: "not", investigation: "investigation bağlandı", update: "güncelleme", rca: "RCA" };

function Timeline({ inc }: { inc: IncidentDetail }) {
  return (
    <ol className="relative space-y-2.5 pl-4">
      <span aria-hidden className="absolute top-1 bottom-1 left-[3px] w-px bg-line" />
      {[...inc.timeline].reverse().map((e) => (
        <li key={e.id} className="relative text-[13px]">
          <span aria-hidden className={`absolute top-1.5 -left-4 size-2 rounded-full border-2 border-surface ${e.kind === "status" ? "bg-ink-2" : "bg-line-strong"}`} />
          <div className="text-xs text-muted">
            {fmtTime(e.at)} · {KIND_TR[e.kind] ?? e.kind}
          </div>
          {e.to_status && (
            <div className="text-ink">
              {e.from_status ? `${INC_STATUS_LABEL[e.from_status as IncidentStatus] ?? e.from_status} → ` : ""}
              {INC_STATUS_LABEL[e.to_status as IncidentStatus] ?? e.to_status}
            </div>
          )}
          {e.message && <div className="text-xs whitespace-pre-wrap text-ink-2">{e.message}</div>}
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
            <Label htmlFor="e-title">Başlık</Label>
            <Input id="e-title" value={f.title} maxLength={300} onChange={(e) => setF({ ...f, title: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="e-sev">Önem</Label>
            <Select id="e-sev" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value as Severity })}>
              {(Object.keys(SEVERITY_LABEL) as Severity[]).map((s) => (
                <option key={s} value={s}>
                  {SEVERITY_LABEL[s]}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div>
          <Label htmlFor="e-desc">Açıklama</Label>
          <Textarea id="e-desc" rows={3} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        </div>
        <div>
          <Label htmlFor="e-rc">Root Cause</Label>
          <Textarea id="e-rc" rows={3} value={f.root_cause} onChange={(e) => setF({ ...f, root_cause: e.target.value })} />
        </div>
        <div>
          <Label htmlFor="e-act">Önerilen aksiyonlar (satır başına bir)</Label>
          <Textarea id="e-act" rows={4} value={f.actions} onChange={(e) => setF({ ...f, actions: e.target.value })} />
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
            <Label htmlFor="a-type">Aksiyon</Label>
            <Select id="a-type" value={f.action_type} onChange={(e) => setF({ ...f, action_type: e.target.value })}>
              {ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="a-target">Hedef</Label>
            <Input id="a-target" value={f.target} maxLength={200} onChange={(e) => setF({ ...f, target: e.target.value })} />
          </div>
        </div>
        <div>
          <Label htmlFor="a-just">Gerekçe (en az 10 karakter)</Label>
          <Textarea id="a-just" rows={4} value={f.justification} onChange={(e) => setF({ ...f, justification: e.target.value })} />
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
      <Page>
        <Skeleton className="mb-4 h-8 w-1/2" />
        <Skeleton className="h-64" />
      </Page>
    );
  const trigger = inc.investigations.find((i) => i.relation === "trigger") ?? inc.investigations[0];
  const durationMs = (inc.resolved_at ? new Date(inc.resolved_at).getTime() : Date.now()) - new Date(inc.detected_at).getTime();

  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Incident'lar", to: "/incidents" }, { label: shortId(inc.id) }]}
        title={inc.title}
        meta={
          <>
            <IncidentStatusBadge status={inc.status} />
            <SeverityBadge severity={inc.severity} />
            {inc.application_id && <AppTag id={inc.application_id} />}
            <span className="tabular-nums">tespit {fmtTime(inc.detected_at)}</span>
            <span>
              {inc.resolved_at ? "çözüm süresi" : "açık kalma"} {fmtDuration(durationMs)}
            </span>
          </>
        }
        actions={
          sre && (
            <>
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
            </>
          )
        }
      />

      <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="divide-y divide-line rounded-lg border border-line bg-surface">
          <section className="px-4 py-3.5">
            <SectionTitle>Özet</SectionTitle>
            {inc.description ? <Markdown>{inc.description}</Markdown> : <p className="text-[13px] text-muted">Açıklama yok.</p>}
            {inc.conditions.length > 0 && <p className="mt-2 text-xs text-muted">Watch koşulları: {inc.conditions.join(", ")}</p>}
          </section>
          <section className="px-4 py-3.5">
            <SectionTitle>Root Cause</SectionTitle>
            <p className={inc.root_cause ? "text-[13px] text-ink" : "text-[13px] text-muted"}>{inc.root_cause ?? "Henüz belirlenmedi."}</p>
          </section>
          <section className="px-4 py-3.5">
            <SectionTitle>Recommended Actions</SectionTitle>
            {inc.recommended_actions.length ? (
              <ol className="space-y-1 text-[13px] text-ink">
                {inc.recommended_actions.map((a, i) => (
                  <li key={a} className="flex gap-2">
                    <span className="w-4 shrink-0 text-right text-xs text-muted tabular-nums">{i + 1}.</span>
                    {a}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-[13px] text-muted">—</p>
            )}
          </section>
          <section className="py-3.5">
            <SectionTitle
              className="px-4"
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
                    <Input aria-label="investigation ID" value={linkId} onChange={(e) => setLinkId(e.target.value)} placeholder="investigation ID / bağlantı" className="h-7 w-56 text-xs" />
                    <Button size="sm" variant="outline" type="submit" loading={link.isPending}>
                      <Link2 /> Bağla
                    </Button>
                  </form>
                )
              }
            >
              İlgili investigation'lar
            </SectionTitle>
            {inc.investigations.length ? (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Investigation</th>
                    <th>İlişki</th>
                    <th>Problem</th>
                    <th className="num">Risk</th>
                    <th>Durum</th>
                  </tr>
                </thead>
                <tbody>
                  {inc.investigations.map((i) => (
                    <tr key={i.id}>
                      <td className="primary max-w-sm">
                        <Link to={`/investigations/${i.id}`} className="block truncate hover:underline">
                          {i.title}
                        </Link>
                        <div className="text-2xs text-muted">{fmtRelative(i.created_at)}</div>
                      </td>
                      <td className="text-xs">{i.relation}</td>
                      <td className="text-xs">{mechanismLabel(i.primary_mechanism)}</td>
                      <td className="num">
                        <RiskValue score={toNum(i.risk_score)} />
                      </td>
                      <td>
                        <InvestigationStatusBadge status={i.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="px-4 text-[13px] text-muted">Bağlı investigation yok.</p>
            )}
          </section>
          <section className="py-3.5">
            <SectionTitle
              className="px-4"
              actions={
                sre &&
                trigger &&
                (trigger.status === "COMPLETED" || trigger.status === "PARTIAL") && (
                  <Button size="sm" variant="outline" loading={gen.isPending} onClick={() => gen.mutate({ investigationId: trigger.id, incident_id: inc.id }, { onSuccess: (r) => nav(`/rca/${r.id}`) })}>
                    <FileText /> RCA üret
                  </Button>
                )
              }
            >
              RCA raporları
            </SectionTitle>
            {inc.rca_reports.length ? (
              <ul className="divide-y divide-line border-t border-line">
                {inc.rca_reports.map((r) => (
                  <li key={r.id}>
                    <Link to={`/rca/${r.id}`} className="flex items-center justify-between gap-3 px-4 py-2 hover:bg-surface-2">
                      <span className="truncate text-[13px] text-ink">{r.title}</span>
                      <span className="shrink-0 text-xs text-muted">
                        {fmtTime(r.generated_at)} · {r.generated_by}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-4 text-[13px] text-muted">RCA raporu yok.</p>
            )}
          </section>
        </div>
        <Card className="xl:sticky xl:top-3">
          <CardHeader title="Incident Timeline" description={`${inc.timeline.length} olay · en yeni üstte`} />
          <CardBody className="space-y-3">
            {sre && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (note.trim()) patch.mutate({ note: note.trim() }, { onSuccess: () => setNote("") });
                }}
                className="space-y-1.5"
              >
                <Textarea aria-label="not" rows={2} value={note} maxLength={4000} onChange={(e) => setNote(e.target.value)} placeholder="Not ekle…" />
                <div className="flex justify-end gap-1">
                  {note && (
                    <Button size="sm" variant="ghost" type="button" onClick={() => setNote("")} aria-label="notu temizle">
                      <X />
                    </Button>
                  )}
                  <Button size="sm" type="submit" disabled={!note.trim()} loading={patch.isPending && !!patch.variables?.note}>
                    <Plus /> Not ekle
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
    </Page>
  );
}

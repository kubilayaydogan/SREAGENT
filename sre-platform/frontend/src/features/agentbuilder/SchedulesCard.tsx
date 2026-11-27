/** Settings › Scheduling: schedule başına agent seçimi. Agent kimliği schedule ayarında saklanır; mevcut health-check
 *  gömülü SRE Agent ile çalışmaya devam eder. Seçilen agent çalıştırılamıyorsa koşu nedeniyle kaydedilir (geçiş yok). */
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Dialog } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { TableWrap } from "@/components/table";
import { api } from "@/services/api";
import { fmtTime } from "@/lib/utils";
import { SYSTEM_AGENT_ID, useAbMutation, useAgents, useSchedules, type Schedule } from "./api";

interface Form {
  name: string;
  agent_id: string;
  interval_minutes: number;
  enabled: boolean;
  input: string;
}

export function AgentSchedulesCard({ canEdit }: { canEdit: boolean }) {
  const sch = useSchedules();
  const agents = useAgents({ mode: "scheduled" });
  const [edit, setEdit] = useState<{ form: Form; create: boolean; system: boolean } | null>(null);
  const save = useAbMutation(
    (e: { form: Form; create: boolean }) =>
      e.create
        ? api.post("/agents/schedules", e.form)
        : api.put(`/agents/schedules/${encodeURIComponent(e.form.name)}`, e.form),
    "Schedule kaydedilemedi",
    { onSuccess: () => setEdit(null) },
  );
  const del = useAbMutation((name: string) => api.del(`/agents/schedules/${encodeURIComponent(name)}`), "Schedule silinemedi");
  const choices = (agents.data?.items ?? []).filter((a) => a.status !== "archived");

  function open(s: Schedule | null) {
    setEdit(
      s
        ? { create: false, system: s.kind === "system", form: { name: s.name, agent_id: s.agent_id ?? SYSTEM_AGENT_ID, interval_minutes: s.interval_minutes, enabled: s.enabled, input: s.input } }
        : { create: true, system: false, form: { name: "", agent_id: choices.find((a) => a.kind === "custom")?.id ?? SYSTEM_AGENT_ID, interval_minutes: 60, enabled: true, input: "" } },
    );
  }

  return (
    <Card>
      <CardHeader
        title="Agent schedule'ları"
        description="Her schedule'ın çalıştıracağı agent. Koşu başında agent'ın aktif yayımlanmış sürümü çözülür; çalıştırılamıyorsa iş geçmişine neden yazılır."
        actions={
          canEdit && (
            <Button size="sm" variant="outline" onClick={() => open(null)}>
              <Plus /> Schedule ekle
            </Button>
          )
        }
      />
      {sch.isLoading ? (
        <TableSkeleton rows={2} cols={5} />
      ) : sch.error ? (
        <ErrorState error={sch.error} onRetry={() => sch.refetch()} />
      ) : !sch.data?.items.length ? (
        <EmptyState title="Schedule yok" />
      ) : (
        <TableWrap>
          <table className="data-table">
            <thead>
              <tr>
                <th>Schedule</th>
                <th>Agent</th>
                <th className="num">Aralık</th>
                <th>Durum</th>
                <th className="num">Sonraki</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sch.data.items.map((s) => (
                <tr key={s.name}>
                  <td className="font-mono text-xs">{s.name}</td>
                  <td className="text-xs">{s.agent_name ?? <span className="text-st-warning-ink">agent bulunamadı</span>}</td>
                  <td className="num text-xs">{s.interval_minutes} dk</td>
                  <td className="text-xs">{s.enabled ? "açık" : "kapalı"}</td>
                  <td className="num text-xs text-muted">{fmtTime(s.next_run_at)}</td>
                  <td className="text-right">
                    {canEdit && (
                      <>
                        <Button size="sm" variant="ghost" aria-label={`${s.name} düzenle`} onClick={() => open(s)}>
                          <Pencil />
                        </Button>
                        {s.kind === "agent" && (
                          <Button size="sm" variant="ghost" aria-label={`${s.name} sil`} onClick={() => del.mutate(s.name)}>
                            <Trash2 />
                          </Button>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {edit && (
        <Dialog
          open
          onOpenChange={(v) => !v && setEdit(null)}
          title={edit.create ? "Agent schedule'ı ekle" : `Schedule: ${edit.form.name}`}
          footer={
            <Button variant="primary" loading={save.isPending} onClick={() => save.mutate(edit)}>
              Kaydet
            </Button>
          }
        >
          {edit.create && (
            <div className="mb-3">
              <Label htmlFor="sch-name">Ad</Label>
              <Input id="sch-name" maxLength={60} value={edit.form.name} onChange={(e) => setEdit({ ...edit, form: { ...edit.form, name: e.target.value } })} placeholder="ör. gece-raporu" />
            </div>
          )}
          <div className="mb-3">
            <Label htmlFor="sch-agent">Agent</Label>
            <Select id="sch-agent" value={edit.form.agent_id} onChange={(e) => setEdit({ ...edit, form: { ...edit.form, agent_id: e.target.value } })} className="w-full">
              {choices.filter((a) => edit.system || a.kind === "custom").map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {a.kind === "system" ? " (gömülü)" : a.published_version_label ? ` · ${a.published_version_label}` : " · yayımlanmamış"}
                </option>
              ))}
            </Select>
            <p className="mt-0.5 text-2xs text-muted">Yalnız zamanlanmış modu destekleyen agent'lar listelenir.</p>
          </div>
          {edit.system ? (
            <p className="text-xs text-muted">Health check sıklığı ve incelenen zaman aralığı Scheduling ayarlarından yönetilir; burada yalnız agent seçilir.</p>
          ) : (
            <>
              <div className="mb-3 grid grid-cols-2 gap-2">
                <div>
                  <Label htmlFor="sch-int">Aralık (dk)</Label>
                  <Input id="sch-int" type="number" min={1} max={10080} value={edit.form.interval_minutes} onChange={(e) => setEdit({ ...edit, form: { ...edit.form, interval_minutes: Number(e.target.value) } })} />
                </div>
                <label className="mt-6 flex items-center gap-2 text-xs text-ink-2">
                  <input type="checkbox" checked={edit.form.enabled} onChange={(e) => setEdit({ ...edit, form: { ...edit.form, enabled: e.target.checked } })} />
                  Açık
                </label>
              </div>
              <Label htmlFor="sch-input">Girdi</Label>
              <Textarea id="sch-input" rows={3} maxLength={4000} value={edit.form.input} onChange={(e) => setEdit({ ...edit, form: { ...edit.form, input: e.target.value } })} className="w-full text-xs" placeholder="boş: agent'ın varsayılan zamanlanmış girdisi" />
            </>
          )}
        </Dialog>
      )}
    </Card>
  );
}

import { Link } from "react-router";
import { JsonView } from "@/components/JsonView";
import { Card, Disclosure, Field } from "@/components/ui/primitives";
import { llmRunText, reasonLabel, sourceLabel } from "@/lib/runconfig";
import type { InvestigationDetail } from "@/types/api";

/** "Bu çalıştırmada kullanılanlar": agent, model (+ seçimin kaynağı), MCP connector. Değerler koşu başında dondurulan
 *  yapılandırma kaydından gelir (investigations.config_snapshot); kayıtta olmayan alan "kaydedilmedi" yazar. */
export function RunConfigCard({ inv, canOpenSettings }: { inv: InvestigationDetail; canOpenSettings?: boolean }) {
  const snap = inv.config_snapshot;
  if (!snap) {
    return (
      <Card className="p-3">
        <div className="text-xs font-medium text-ink">Bu çalıştırmada kullanılanlar</div>
        <p className="mt-1 text-xs text-muted">Bu çalıştırma için yapılandırma kaydı yok (kayıt özelliğinden önce çalışmış).</p>
      </Card>
    );
  }
  const llm = snap.llm;
  const run = llmRunText(llm.run, llm.effective?.rca);
  const hasEffective = !!llm.effective;
  const conn = snap.connector as { connector?: string | null; available?: boolean; query_timeout_s?: number; query_retries?: number; policy?: string };
  const writer = inv.explanation_source;
  return (
    <Card className="p-3">
      <div className="text-xs font-medium text-ink">Bu çalıştırmada kullanılanlar</div>
      <dl className="mt-2 divide-y divide-line border-t border-line">
        <Field label="Agent">Gömülü SRE Agent</Field>
        <Field label={<span title="Model: analiz açıklamasını yazan dil modeli. Risk score ve evidence modelden bağımsız, kuralla hesaplanır.">Model</span>}>
          {!hasEffective ? (
            <span className="text-muted">kaydedilmedi</span>
          ) : run.model ? (
            <span className="font-mono text-2xs">{run.model}</span>
          ) : (
            <span className="text-st-warning-ink">LLM kullanılmadı</span>
          )}
        </Field>
        {writer && <Field label="Açıklamayı yazan">{writer}</Field>}
        <Field label={<span title="MCP connector: agent'ın Dynatrace'ten veri okuduğu bağlantı.">MCP connector</span>}>
          {conn.connector ? (
            <span className={conn.available === false ? "text-st-critical-ink" : undefined}>
              {conn.connector}
              {conn.available === false && " · kullanılamadı"}
            </span>
          ) : (
            <span className="text-st-critical-ink">yok{conn.policy ? ` (${conn.policy})` : ""}</span>
          )}
        </Field>
        {conn.query_timeout_s != null && (
          <Field label="Query sınırı">
            {conn.query_timeout_s} sn timeout · {conn.query_retries ?? 0} retry
          </Field>
        )}
      </dl>
      {hasEffective && (
        <p className="mt-2 text-2xs text-ink-2">
          <span className="text-muted">{run.model ? "Neden bu model? " : "Neden? "}</span>
          {run.model ? `Seçim kaynağı: ${run.detail}.` : `${run.detail}.`}
        </p>
      )}
      {llm.model_override_ignored && (
        <p className="mt-2 text-2xs text-st-warning-ink">Üst bardaki model seçimi izinli model listesinde olmadığı için bu çalıştırmada uygulanmadı.</p>
      )}
      {llm.run?.stage === "answer" && llm.effective?.rca && !llm.effective.rca.model && (
        <p className="mt-2 text-2xs text-muted">Yanıt deterministik özetle üretildi: {reasonLabel(llm.effective.rca.reason)}.</p>
      )}
      <Disclosure title={<span className="text-xs text-ink-2">Teknik ayrıntı</span>} className="mt-1">
        <dl className="mb-2 space-y-0.5 text-2xs text-ink-2">
          <div>
            Soru yorumlama modeli: <span className="font-mono">{llm.effective?.chat?.model ?? "—"}</span> ({sourceLabel(llm.effective?.chat)})
          </div>
          <div>
            Ayar revizyonları:{" "}
            {Object.entries(snap.revisions)
              .map(([k, v]) => `${k} r${v}`)
              .join(", ")}
          </div>
          <div>
            Config hash: <span className="font-mono">{snap.hash}</span> · LangSmith {snap.tracing.langsmith ? "trace yazıldı" : "trace yazılmadı"}
          </div>
        </dl>
        <JsonView value={snap} open={1} maxH="max-h-64" />
        {canOpenSettings && (
          <p className="mt-2 text-2xs text-muted">
            Ayar değişikliği süren çalıştırmayı etkilemez; bir sonraki çalıştırmadan itibaren geçerlidir.{" "}
            <Link to="/settings/llm" className="text-accent-text hover:underline">
              Model ayarlarını aç
            </Link>
          </p>
        )}
      </Disclosure>
    </Card>
  );
}

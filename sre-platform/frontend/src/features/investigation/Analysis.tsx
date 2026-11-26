/* Investigation analizi: incident investigation düzeni (chatbot çıktısı değil).
   Bölümler: Executive Summary · Root Cause · Confidence · Impacted Services · Risk Score · Supporting Evidence ·
   Incident Timeline · Alternative Hypotheses · Recommended Actions · Data Quality.
   Teknik iddialar evidence ile ilişkilendirilir: evidence id → rapordaki DQL özeti → Evidence sekmesinde ham kayıt. */
import { AlertTriangle, ArrowRight, BookOpen, CheckCircle2, CircleSlash, ExternalLink, FileSearch, Link2 } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { AppTag, ConfidenceBadge, CONF_LABEL } from "@/components/badges";
import { Markdown } from "@/components/Markdown";
import { DqlBlock } from "@/components/RecordTables";
import { RiskBar, RiskBreakdown, RiskValue } from "@/components/risk";
import { Button, Disclosure } from "@/components/ui/primitives";
import { useRecommendations } from "@/hooks/queries";
import { type AppRisk } from "@/lib/risk";
import { mechanismLabel, SIGNAL_LABEL } from "@/lib/steps";
import { cn, fmtDuration, fmtMs, fmtNum, fmtPct, fmtTime } from "@/lib/utils";
import type { InvestigationDetail, Mechanism, ReportEvidence } from "@/types/api";

export const SECTIONS = [
  ["summary", "Executive Summary"],
  ["root-cause", "Root Cause"],
  ["confidence", "Confidence"],
  ["impacted", "Impacted Services"],
  ["risk", "Risk Score"],
  ["evidence", "Supporting Evidence"],
  ["timeline", "Incident Timeline"],
  ["alternatives", "Alternative Hypotheses"],
  ["actions", "Recommended Actions"],
  ["quality", "Data Quality"],
] as const;

function Section({ id, title, children, aside }: { id: string; title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section id={`sec-${id}`} aria-labelledby={`h-${id}`} className="scroll-mt-4 border-t border-line px-4 py-3.5 first:border-t-0">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id={`h-${id}`} className="text-[13px] font-semibold text-ink">
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** İçerik türü etiketi: ölçülen bulgu, kuralla hesaplanan değer ve model yorumu arayüzde ayrı görünür. */
function Kind({ k, extra }: { k: "measured" | "computed" | "interpretation"; extra?: ReactNode }) {
  const t = {
    measured: ["Ölçülen bulgu", "Dynatrace'ten okunan veriden çıkarılır; her satır bir evidence kaydına bağlıdır."],
    computed: ["Kuralla hesaplanır", "Deterministik hesap: aynı veriyle her zaman aynı sonuç. Dil modeli bu değeri değiştiremez."],
    interpretation: ["Agent yorumu", "Bulguların açıklamasıdır; ölçüm değildir. Dayandığı evidence metinde belirtilir, belirsizlik Confidence bölümündedir."],
  }[k];
  return (
    <span className="text-2xs text-muted" title={t[1]}>
      {t[0]}
      {extra}
    </span>
  );
}

function Muted({ children }: { children: ReactNode }) {
  return <p className="text-[13px] text-muted">{children}</p>;
}

/** Kanıt referansı: tıklanınca Evidence sekmesinde ilgili satır açılır. */
export function EvidenceRef({ id, onOpen, ev }: { id: string; onOpen: (id: string) => void; ev?: ReportEvidence }) {
  const bad = ev && ev.status !== "OK" && ev.status !== "EMPTY";
  return (
    <button
      type="button"
      onClick={() => onOpen(id)}
      title={ev ? `${SIGNAL_LABEL[ev.signal] ?? ev.signal} · ${ev.status} · ${ev.records ?? "—"} kayıt` : "Evidence'ı aç"}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-px font-mono text-2xs hover:border-accent hover:text-ink",
        bad ? "border-st-warning/50 text-st-warning-ink" : "border-line-strong text-ink-2",
      )}
    >
      <Link2 className="size-3" />
      {ev ? (SIGNAL_LABEL[ev.signal] ?? ev.signal) : id}
    </button>
  );
}

function ExecutiveSummary({ inv }: { inv: InvestigationDetail }) {
  const [full, setFull] = useState(false);
  const text = inv.explanation?.trim();
  const r = inv.report ?? {};
  if (!text) {
    const fallback = inv.root_cause_hypothesis ?? r.human_review?.[0];
    return fallback ? <p className="text-[13px] text-ink">{fallback}</p> : <Muted>Özet üretilmedi.</Muted>;
  }
  const long = text.length > 900;
  return (
    <div>
      <div className={cn("relative", long && !full && "max-h-56 overflow-hidden")}>
        <Markdown>{text}</Markdown>
      </div>
      {long && (
        <Button size="sm" variant="ghost" className="mt-1 -ml-2" onClick={() => setFull((f) => !f)} aria-expanded={full}>
          {full ? "Daralt" : "Tamamını göster"}
        </Button>
      )}
    </div>
  );
}

function LockChain({ m }: { m: Mechanism }) {
  const chain = m.lock_chain ?? [];
  if (!chain.length) return null;
  return (
    <div className="mt-2">
      <div className="mb-1 text-xs font-medium text-muted">Lock chain</div>
      <ol className="space-y-0.5 text-xs">
        {chain.map((n) => (
          <li key={n.app} className="flex items-center gap-1.5" style={{ paddingLeft: n.level * 16 }}>
            {n.level > 0 && <span className="text-muted">└</span>}
            <AppTag id={n.app} />
            {n.level === 0 ? (
              <span className="text-muted">blocker (source)</span>
            ) : (
              <span className="text-muted tabular-nums">
                bekler → {n.waits_on.map((w) => `${w.app.replace(/-service$/, "")} (${fmtNum(w.n)})`).join(", ")}
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function AppList({ ids }: { ids?: string[] }) {
  if (!ids?.length) return <span className="text-muted">—</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2.5 gap-y-1">
      {ids.map((a) => (
        <AppTag key={a} id={a} />
      ))}
    </span>
  );
}

function RootCause({ inv, evIndex, onEvidence }: { inv: InvestigationDetail; evIndex: Map<string, ReportEvidence>; onEvidence: (id: string) => void }) {
  const mechs = inv.report?.mechanisms ?? [];
  const primary = mechs.find((m) => m.id === inv.primary_mechanism) ?? mechs[0];
  if (!primary)
    return (
      <div>
        <p className="text-[13px] text-ink">{inv.root_cause_hypothesis ?? "Tanımlı mechanism'lerle açıklanamadı."}</p>
        <p className="mt-1 text-xs text-muted">Baseline'a göre anlamlı bir bozulma veya tanımlı bir mechanism bulunamadı.</p>
      </div>
    );
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[15px] font-semibold text-ink">{primary.title ?? mechanismLabel(primary.id)}</span>
        {primary.chain_path && <span className="font-mono text-xs text-ink-2">{primary.chain_path}</span>}
      </div>
      {inv.root_cause_hypothesis && inv.root_cause_hypothesis !== primary.title && <p className="text-[13px] text-ink">{inv.root_cause_hypothesis}</p>}
      {primary.mechanism && !inv.root_cause_hypothesis?.includes(primary.mechanism) && <p className="text-[13px] leading-relaxed text-ink-2">{primary.mechanism}</p>}
      <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-[auto_1fr]">
        <dt className="text-muted">Source</dt>
        <dd>
          <AppList ids={primary.source_apps} />
        </dd>
        <dt className="text-muted">Etkilenen</dt>
        <dd>
          <AppList ids={primary.affected_apps} />
        </dd>
        {!!primary.signals?.length && (
          <>
            <dt className="text-muted">Dayanak sinyaller</dt>
            <dd className="text-ink-2">{primary.signals.join(" · ")}</dd>
          </>
        )}
        {!!primary.evidence_ids?.length && (
          <>
            <dt className="text-muted">Evidence</dt>
            <dd className="flex flex-wrap gap-1">
              {primary.evidence_ids.map((e) => (
                <EvidenceRef key={e} id={e} ev={evIndex.get(e)} onOpen={onEvidence} />
              ))}
            </dd>
          </>
        )}
      </dl>
      <LockChain m={primary} />
      <p className="text-2xs text-muted">Korelasyon temelli hipotezdir; değişiklik öncesi doğrulanmalıdır.</p>
    </div>
  );
}

function Confidence({ inv }: { inv: InvestigationDetail }) {
  const mechs = inv.report?.mechanisms ?? [];
  const cov = inv.report?.data_coverage;
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <ConfidenceBadge confidence={inv.confidence} />
        {cov?.signals_total != null && (
          <span className="text-xs text-muted tabular-nums">
            {cov.signals_ok}/{cov.signals_total} sinyal başarılı · coverage {fmtPct(cov.ratio, 0)}
          </span>
        )}
      </div>
      {mechs.length > 1 && (
        <ul className="text-xs text-ink-2">
          {mechs.map((m) => (
            <li key={m.id} className="flex justify-between gap-3 py-0.5">
              <span>
                {m.title ?? mechanismLabel(m.id)}
                {m.id === inv.primary_mechanism && <span className="ml-1.5 text-muted">(birincil)</span>}
              </span>
              <span className="text-muted">{m.confidence ? CONF_LABEL[m.confidence] : "—"}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-2xs text-muted">Güven, mechanism kurallarının karşılanan evidence sayısı ve data coverage'dan deterministik olarak türetilir.</p>
    </div>
  );
}

function Impacted({ inv }: { inv: InvestigationDetail }) {
  const r = inv.report ?? {};
  const impacted = Object.entries(r.impacted ?? {});
  const unexplained = new Set(r.unexplained_impacted ?? []);
  const riskApps = (r.risk?.apps ?? {}) as Record<string, AppRisk>;
  if (!impacted.length) return <Muted>Baseline'a göre etkilenen servis tespit edilmedi.</Muted>;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Servis</th>
          <th>Bulgu</th>
          <th className="num">Latency / baseline</th>
          <th className="num">Risk</th>
        </tr>
      </thead>
      <tbody>
        {impacted.map(([a, v]) => (
          <tr key={a}>
            <td>
              <AppTag id={a} className="text-[13px] text-ink" />
              {unexplained.has(a) && (
                <div className="mt-0.5 inline-flex items-center gap-1 text-2xs text-st-warning-ink">
                  <AlertTriangle className="size-3" /> mechanism ile açıklanamadı
                </div>
              )}
            </td>
            <td className="text-xs">{(v.reasons ?? []).join("; ") || "—"}</td>
            <td className="num">{v.latency_ratio != null ? `×${fmtNum(v.latency_ratio, 1)}` : "—"}</td>
            <td className="num">
              <RiskValue score={riskApps[a]?.score} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RiskSection({ inv }: { inv: InvestigationDetail }) {
  const risk = inv.report?.risk;
  const apps = Object.entries((risk?.apps ?? {}) as Record<string, AppRisk>).sort(([, a], [, b]) => (b.score ?? -1) - (a.score ?? -1));
  if (!risk) return <Muted>Bu kayıtta risk bloğu yok.</Muted>;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <span>
          Overall <RiskValue score={risk.overall?.score} showBand className="ml-1 text-[13px]" />
        </span>
        <span className="tabular-nums">
          {risk.overall?.scored_apps ?? "—"}/{risk.overall?.total_apps ?? "—"} servis skorlandı
        </span>
        <span>overall = en yüksek servis skoru</span>
      </div>
      <div className="divide-y divide-line rounded-md border border-line">
        {apps.map(([id, a]) => (
          <Disclosure
            key={id}
            className="px-2"
            title={
              <span className="grid grid-cols-[8rem_1fr_3rem] items-center gap-3">
                <AppTag id={id} />
                <RiskBar score={a.score} />
                <RiskValue score={a.score} className="text-right" />
              </span>
            }
          >
            <RiskBreakdown risk={a} />
          </Disclosure>
        ))}
      </div>
    </div>
  );
}

const CMP_METRICS: { k: string; label: string; fmt: (v: number | null) => string; higherIsWorse: boolean }[] = [
  { k: "rpm", label: "Throughput (istek/dk)", fmt: (v) => fmtNum(v, 1), higherIsWorse: false },
  { k: "err_rate", label: "Error rate", fmt: (v) => fmtPct(v, 2), higherIsWorse: true },
  { k: "span_p95_ms", label: "P95 latency (span)", fmt: fmtMs, higherIsWorse: true },
  { k: "http_mean_ms", label: "Ortalama latency", fmt: fmtMs, higherIsWorse: true },
  { k: "pool_ratio", label: "Connection pool", fmt: (v) => fmtPct(v, 0), higherIsWorse: true },
  { k: "lock_waiters_avg", label: "Lock wait (ort. session)", fmt: (v) => fmtNum(v, 2), higherIsWorse: true },
];

/** Baseline ↔ analiz penceresi: aynı metric, iki değer, değişim. Baseline ve güncel ayrı sütunlardadır. */
function BaselineCompare({ inv }: { inv: InvestigationDetail }) {
  const fs = inv.report?.features_summary;
  const apps = Object.keys(fs?.window?.apps ?? {}).sort();
  const [app, setApp] = useState(() => inv.affected_apps[0] ?? inv.source_apps[0] ?? apps[0]);
  if (!fs?.window?.apps || !fs.baseline?.apps || !apps.length) return <Muted>Baseline karşılaştırması bu kayıtta yok.</Muted>;
  const cur = fs.window.apps[app] ?? {};
  const base = fs.baseline.apps[app] ?? {};
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1" role="tablist" aria-label="servis">
        {apps.map((a) => (
          <button
            key={a}
            role="tab"
            aria-selected={a === app}
            onClick={() => setApp(a)}
            className={cn("rounded-sm border px-1.5 py-0.5", a === app ? "border-line-strong bg-surface-3" : "border-transparent hover:bg-surface-2")}
          >
            <AppTag id={a} />
          </button>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th className="num">Baseline</th>
            <th className="num">Analiz penceresi</th>
            <th className="num">Değişim</th>
          </tr>
        </thead>
        <tbody>
          {CMP_METRICS.map((m) => {
            const b = typeof base[m.k] === "number" ? (base[m.k] as number) : null;
            const c = typeof cur[m.k] === "number" ? (cur[m.k] as number) : null;
            const ratio = b != null && c != null && b > 0 ? c / b : null;
            const worse = ratio != null && (m.higherIsWorse ? ratio >= 1.2 : ratio <= 0.8);
            return (
              <tr key={m.k}>
                <td className="text-xs">{m.label}</td>
                <td className="num text-muted">{m.fmt(b)}</td>
                <td className="num text-ink">{m.fmt(c)}</td>
                <td className={cn("num text-xs", worse ? "font-medium text-st-serious-ink" : "text-muted")}>
                  {ratio == null ? (b === 0 && c != null && c > 0 ? "yeni" : "—") : `×${fmtNum(ratio, 2)}`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SupportingEvidence({ inv, evIndex, onEvidence }: { inv: InvestigationDetail; evIndex: Map<string, ReportEvidence>; onEvidence: (id: string) => void }) {
  const mechs = inv.report?.mechanisms ?? [];
  const linked = new Set(mechs.flatMap((m) => m.evidence_ids ?? []));
  const evs = [...evIndex.values()];
  const support = evs.filter((e) => linked.has(e.id));
  const rest = evs.filter((e) => !linked.has(e.id));
  return (
    <div className="space-y-3">
      {mechs.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-ink">Mechanism'i destekleyen evidence</div>
          {support.length ? (
            <div className="divide-y divide-line rounded-md border border-line">
              {support.map((e) => (
                <EvidenceRow key={e.id} e={e} onOpen={onEvidence} />
              ))}
            </div>
          ) : (
            <Muted>Mechanism'e bağlı evidence kaydı yok.</Muted>
          )}
        </div>
      )}
      <div>
        <div className="mb-1 text-xs font-medium text-ink">Baseline karşılaştırması</div>
        <BaselineCompare inv={inv} />
      </div>
      {rest.length > 0 && (
        <Disclosure title={<span className="text-xs text-ink-2">Diğer toplanan sinyaller</span>} meta={`${rest.length} DQL query`}>
          <div className="divide-y divide-line rounded-md border border-line">
            {rest.map((e) => (
              <EvidenceRow key={e.id} e={e} onOpen={onEvidence} />
            ))}
          </div>
        </Disclosure>
      )}
    </div>
  );
}

function EvidenceRow({ e, onOpen }: { e: ReportEvidence; onOpen: (id: string) => void }) {
  const ok = e.status === "OK" || e.status === "EMPTY";
  return (
    <Disclosure
      className="px-2"
      title={
        <span className="flex items-center gap-2 text-xs">
          <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", ok ? "bg-st-good" : "bg-st-warning")} />
          <span className="text-ink">{SIGNAL_LABEL[e.signal] ?? e.signal}</span>
          <span className="font-mono text-2xs text-muted">{e.id}</span>
        </span>
      }
      meta={
        <span className="tabular-nums">
          {e.status}
          {e.records != null && ` · ${e.records} kayıt`}
        </span>
      }
    >
      <div className="space-y-2">
        {e.error && <p className="font-mono text-2xs break-all text-st-critical-ink">{e.error}</p>}
        {e.dql && <DqlBlock dql={e.dql} />}
        <div className="flex flex-wrap items-center gap-3 text-2xs text-muted">
          {e.dynatrace_query_id && <span className="font-mono">queryId {e.dynatrace_query_id}</span>}
          <button onClick={() => onOpen(e.id)} className="inline-flex items-center gap-1 text-accent-text hover:underline">
            <FileSearch className="size-3" /> Ham evidence kaydını aç
          </button>
        </div>
      </div>
    </Disclosure>
  );
}

function Timeline({ inv }: { inv: InvestigationDetail }) {
  const r = inv.report ?? {};
  const base = r.windows?.find((w) => w.name === "baseline");
  const win = r.windows?.find((w) => w.name === "window");
  type Item = { at: string | null; label: string; detail?: string; tone?: "muted" | "ink" | "warn" };
  const all: Item[] = [
    { at: base?.start ?? inv.baseline_start, label: "Baseline penceresi başlar", detail: base || inv.baseline_end ? `→ ${fmtTime(base?.end ?? inv.baseline_end, false)}` : undefined, tone: "muted" },
    { at: win?.start ?? inv.window_start, label: "Analiz penceresi başlar", detail: `→ ${fmtTime(win?.end ?? inv.window_end, false)}`, tone: "ink" },
    ...Object.entries(r.impacted ?? {}).map(([a, v]): Item => ({ at: null, label: `${a.replace(/-service$/, "")} etkilendi`, detail: (v.reasons ?? []).join("; "), tone: "warn" })),
    { at: inv.started_at ?? inv.created_at, label: "Investigation başladı", tone: "muted" },
    { at: inv.completed_at, label: inv.status === "FAILED" ? "Investigation başarısız" : "Investigation tamamlandı", detail: inv.duration_ms != null ? fmtDuration(inv.duration_ms) : undefined, tone: "muted" },
  ];
  const items = all.filter((x) => x.at || x.tone === "warn");
  return (
    <ol className="relative space-y-2 border-l border-line pl-4">
      {items.map((x, i) => (
        <li key={i} className="relative text-xs">
          <span
            aria-hidden
            className={cn(
              "absolute top-1 -left-[1.3rem] size-2 rounded-full border-2 border-surface",
              x.tone === "warn" ? "bg-st-warning" : x.tone === "ink" ? "bg-ink" : "bg-line-strong",
            )}
          />
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="w-28 shrink-0 text-muted tabular-nums">{x.at ? fmtTime(x.at) : "pencere içinde"}</span>
            <span className={x.tone === "muted" ? "text-ink-2" : "text-ink"}>{x.label}</span>
            {x.detail && <span className="text-muted">{x.detail}</span>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Alternatives({ inv }: { inv: InvestigationDetail }) {
  const r = inv.report ?? {};
  const secondary = (r.mechanisms ?? []).filter((m) => m.id !== inv.primary_mechanism);
  const alts = r.alternatives ?? [];
  if (!secondary.length && !alts.length) return <Muted>Alternatif hipotez yok: veri tek bir açıklamayı destekliyor ya da mechanism tespit edilmedi.</Muted>;
  return (
    <ul className="space-y-1.5 text-[13px]">
      {secondary.map((m) => (
        <li key={m.id} className="flex gap-2">
          <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-muted" />
          <div>
            <span className="text-ink">{m.title ?? mechanismLabel(m.id)}</span>
            {m.secondary_to && <span className="ml-1.5 text-xs text-muted">secondary → {mechanismLabel(m.secondary_to)}</span>}
            {m.confidence && <span className="ml-1.5 text-xs text-muted">· {CONF_LABEL[m.confidence]}</span>}
            {m.mechanism && <p className="text-xs text-ink-2">{m.mechanism}</p>}
          </div>
        </li>
      ))}
      {alts.map((a) => (
        <li key={a} className="flex gap-2 text-ink-2">
          <CircleSlash className="mt-0.5 size-3.5 shrink-0 text-muted" />
          {a}
        </li>
      ))}
    </ul>
  );
}

function Actions({ inv }: { inv: InvestigationDetail }) {
  const rec = useRecommendations(inv.id);
  const review = inv.report?.human_review ?? [];
  const kb = rec.data;
  return (
    <div className="space-y-3">
      {review.length ? (
        <ol className="space-y-1 text-[13px] text-ink">
          {review.map((x, i) => (
            <li key={x} className="flex gap-2">
              <span className="w-4 shrink-0 text-right text-xs text-muted tabular-nums">{i + 1}.</span>
              {x}
            </li>
          ))}
        </ol>
      ) : (
        <Muted>Rapor bir eylem önermedi.</Muted>
      )}
      {kb && (kb.runbooks.length > 0 || kb.known_issues.length > 0) && (
        <div>
          <div className="mb-1 text-xs font-medium text-ink">Bilgi bankasından eşleşenler</div>
          <ul className="divide-y divide-line rounded-md border border-line text-[13px]">
            {kb.runbooks.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-3 px-2.5 py-1.5">
                <Link to={`/knowledge?tab=runbooks&id=${r.id}`} className="inline-flex min-w-0 items-center gap-1.5 text-ink hover:underline">
                  <BookOpen className="size-3.5 shrink-0 text-muted" />
                  <span className="truncate">{r.title}</span>
                </Link>
                <span className="shrink-0 text-xs text-muted">
                  Runbook · risk {r.risk_level}
                  {r.requires_approval && " · onay gerekli"}
                </span>
              </li>
            ))}
            {kb.known_issues.map((k) => (
              <li key={k.id} className="flex items-center justify-between gap-3 px-2.5 py-1.5">
                <Link to={`/knowledge?tab=issues&id=${k.id}`} className="inline-flex min-w-0 items-center gap-1.5 text-ink hover:underline">
                  <ExternalLink className="size-3.5 shrink-0 text-muted" />
                  <span className="truncate">{k.title}</span>
                </Link>
                <span className="shrink-0 text-xs text-muted">Bilinen sorun</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DataQuality({ inv, evIndex }: { inv: InvestigationDetail; evIndex: Map<string, ReportEvidence> }) {
  const r = inv.report ?? {};
  const cov = r.data_coverage;
  const failed = [...evIndex.values()].filter((e) => e.status !== "OK" && e.status !== "EMPTY");
  const gaps = r.data_gaps ?? [];
  const zero = r.true_zero_signals ?? [];
  const b = r.budget;
  return (
    <div className="space-y-2.5 text-xs">
      <div className="flex items-center gap-3">
        <div className="h-1.5 w-40 rounded-full bg-surface-3" aria-hidden>
          <div className={cn("h-full rounded-full", (cov?.ratio ?? 0) >= 0.9 ? "bg-st-good" : "bg-st-warning")} style={{ width: `${Math.round((cov?.ratio ?? 0) * 100)}%` }} />
        </div>
        <span className="text-ink tabular-nums">
          {cov?.signals_total != null ? `${cov.signals_ok}/${cov.signals_total} sinyal` : "—"} · {fmtPct(cov?.ratio ?? (inv.data_coverage == null ? null : Number(inv.data_coverage)), 0)} coverage
        </span>
      </div>
      {gaps.length > 0 ? (
        <ul className="space-y-0.5">
          {gaps.map((g) => (
            <li key={g} className="flex gap-1.5 text-ink-2">
              <AlertTriangle className="mt-px size-3.5 shrink-0 text-st-warning" />
              {g}
            </li>
          ))}
        </ul>
      ) : (
        <p className="inline-flex items-center gap-1.5 text-ink-2">
          <CheckCircle2 className="size-3.5 text-st-good" /> Data gap yok
        </p>
      )}
      {failed.length > 0 && (
        <p className="text-ink-2">
          Başarısız / sorunlu query:  {failed.map((e) => `${SIGNAL_LABEL[e.signal] ?? e.signal} (${e.status})`).join(", ")}
        </p>
      )}
      {zero.length > 0 && (
        <p className="text-muted">Gerçek sıfır (collector çalıştı, olay yok): {zero.map((z) => SIGNAL_LABEL[z] ?? z).join(", ")}</p>
      )}
      {b && (
        <p className="text-muted tabular-nums">
          {b.queries_used ?? "—"} DQL query · {b.llm_calls ?? 0} LLM çağrısı
          {b.input_tokens != null && ` · ${fmtNum((b.input_tokens ?? 0) + (b.output_tokens ?? 0))} token`} · {b.elapsed_s != null ? `${fmtNum(b.elapsed_s, 1)} sn` : "—"}
        </p>
      )}
    </div>
  );
}

export function Analysis({ inv, onEvidence }: { inv: InvestigationDetail; onEvidence: (id: string) => void }) {
  const evIndex = useMemo(() => new Map((inv.report?.evidence ?? []).map((e) => [e.id, e])), [inv.report?.evidence]);
  return (
    <div className="rounded-lg border border-line bg-surface">
      <Section id="summary" title="Executive Summary" aside={<Kind k="interpretation" extra={inv.explanation_source ? ` · yazan: ${inv.explanation_source}` : undefined} />}>
        <ExecutiveSummary inv={inv} />
      </Section>
      <Section id="root-cause" title="Root Cause" aside={<Kind k="interpretation" extra=" · hipotez" />}>
        <RootCause inv={inv} evIndex={evIndex} onEvidence={onEvidence} />
      </Section>
      <Section id="confidence" title="Confidence">
        <Confidence inv={inv} />
      </Section>
      <Section id="impacted" title="Impacted Services">
        <Impacted inv={inv} />
      </Section>
      <Section id="risk" title="Risk Score" aside={<Kind k="computed" />}>
        <RiskSection inv={inv} />
      </Section>
      <Section id="evidence" title="Supporting Evidence" aside={<Kind k="measured" />}>
        <SupportingEvidence inv={inv} evIndex={evIndex} onEvidence={onEvidence} />
      </Section>
      <Section id="timeline" title="Incident Timeline">
        <Timeline inv={inv} />
      </Section>
      <Section id="alternatives" title="Alternative Hypotheses">
        <Alternatives inv={inv} />
      </Section>
      <Section id="actions" title="Recommended Actions">
        <Actions inv={inv} />
      </Section>
      <Section id="quality" title="Data Quality">
        <DataQuality inv={inv} evIndex={evIndex} />
      </Section>
    </div>
  );
}

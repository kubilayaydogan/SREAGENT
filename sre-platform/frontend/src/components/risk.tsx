/* Risk score gösterimi: 0–100 sayı + bant; renk yalnız dikkat gerektiren bantlarda. Dekoratif gauge yok.
   Breakdown, backend'in hesapladığı bileşenleri (0–1) ağırlık ve katkı puanıyla açıklar — yeniden hesaplamaz. */
import { cn, fmtNum, fmtPct } from "@/lib/utils";
import {
  bandOf,
  BAND_FILL,
  BAND_LABEL,
  BAND_TEXT,
  COMPONENT_HINT,
  COMPONENT_LABEL,
  contributions,
  RISK_COMPONENTS,
  RISK_WEIGHTS,
  type AppRisk,
} from "@/lib/risk";

export function RiskValue({ score, showBand, className }: { score: number | null | undefined; showBand?: boolean; className?: string }) {
  const b = bandOf(score);
  if (score == null || !b)
    return (
      <span className={cn("text-muted", className)} title="Risk score hesaplanamadı (data coverage yetersiz)">
        —
      </span>
    );
  return (
    <span className={cn("tabular-nums", BAND_TEXT[b], b !== "low" && "font-medium", className)} title={`${BAND_LABEL[b]} bant (0–100)`}>
      {fmtNum(score, 0)}
      {showBand && <span className="ml-1 text-xs font-normal">{BAND_LABEL[b]}</span>}
    </span>
  );
}

/** 0–100 yatay ölçek: dolgu = skor, bant eşikleri ince işaret. Satır içi; büyük gauge yerine. */
export function RiskBar({ score, className }: { score: number | null | undefined; className?: string }) {
  const b = bandOf(score);
  return (
    <div className={cn("relative h-1.5 w-full rounded-full bg-surface-3", className)} aria-hidden>
      {score != null && b && <div className={cn("h-full rounded-full", BAND_FILL[b])} style={{ width: `${Math.max(1.5, Math.min(100, score))}%` }} />}
      {[25, 50, 75].map((x) => (
        <span key={x} className="absolute top-0 h-full w-px bg-surface" style={{ left: `${x}%` }} />
      ))}
    </div>
  );
}

/** Bileşen kırılımı. Katkı = 100·w·v / Σw(mevcut). Eksik bileşen açıkça "veri yok" yazar. */
export function RiskBreakdown({ risk, compact }: { risk: AppRisk; compact?: boolean }) {
  const contrib = contributions(risk);
  const c = risk.components;
  const ratio = typeof c._latency_ratio === "number" ? c._latency_ratio : null;
  return (
    <div className={cn("grid gap-3", !compact && "lg:grid-cols-[minmax(0,1fr)_16rem]")}>
      <table className="w-full text-xs" aria-label="risk bileşenleri">
        <thead>
          <tr className="text-left text-2xs text-muted">
            <th className="py-1 pr-3 font-medium">Bileşen</th>
            <th className="py-1 pr-3 text-right font-medium">Normalize</th>
            <th className="py-1 pr-3 text-right font-medium">Ağırlık</th>
            <th className="w-1/3 py-1 pr-3 font-medium">Skora katkı</th>
          </tr>
        </thead>
        <tbody>
          {RISK_COMPONENTS.map((k) => {
            const v = c[k];
            const has = typeof v === "number";
            const pts = contrib[k] ?? 0;
            return (
              <tr key={k} className="border-t border-line" title={COMPONENT_HINT[k]}>
                <td className="py-1 pr-3 text-ink-2">
                  {COMPONENT_LABEL[k]}
                  {k === "latency" && ratio != null && <span className="ml-1.5 text-muted">×{fmtNum(ratio, 2)} baseline</span>}
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">{has ? fmtNum(v as number, 2) : <span className="text-muted">veri yok</span>}</td>
                <td className="py-1 pr-3 text-right text-muted tabular-nums">{fmtPct(RISK_WEIGHTS[k], 0)}</td>
                <td className="py-1 pr-3">
                  <div className="flex items-center gap-2">
                    <div className="h-1 flex-1 rounded-full bg-surface-3">
                      {has && pts > 0 && <div className="h-full rounded-full bg-ink-2" style={{ width: `${Math.min(100, pts)}%` }} />}
                    </div>
                    <span className="w-8 text-right tabular-nums text-ink">{has ? fmtNum(pts, 1) : "—"}</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!compact && (
        <div className="space-y-1.5 text-xs text-muted">
          <div className="flex items-baseline justify-between">
            <span>Skor</span>
            <RiskValue score={risk.score} showBand className="text-[13px]" />
          </div>
          <RiskBar score={risk.score} />
          <div className="flex items-baseline justify-between pt-1">
            <span>Bileşen kapsamı</span>
            <span className="text-ink-2 tabular-nums">{fmtPct(risk.coverage, 0)}</span>
          </div>
          {risk.missing?.length > 0 && <p>Eksik: {risk.missing.map((m) => COMPONENT_LABEL[m as keyof typeof COMPONENT_LABEL] ?? m).join(", ")}</p>}
          <p className="pt-1 leading-snug">Skor = ağırlıklı ortalama × 100. Bileşen eşikleri satır üzerine gelince görünür.</p>
        </div>
      )}
    </div>
  );
}

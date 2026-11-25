import { CircleHelp } from "lucide-react";
import { useMemo } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { appColor, appLabel, APP_ORDER, CHART } from "@/lib/colors";
import { fmtTime } from "@/lib/utils";

export interface SeriesDef {
  id: string;
  label?: string;
  color?: string;
  /** baseline / karşılaştırma serisi: kesikli, nötr gri, lejantta "baseline" */
  baseline?: boolean;
  points: { t: string; v: number | null }[];
}

export interface RefLine {
  y: number;
  label: string;
}

/** Zaman serisi: TEK y ekseni, 1.5px çizgi, eksik nokta = boşluk (asla 0 değil), lejant (≥2 seri) + crosshair tooltip.
 *  minPoints altında veri varsa yanıltıcı çizgi yerine "yetersiz veri" gösterilir. */
export function TimeSeriesChart({
  series,
  format,
  height = 220,
  emptyText = "Bu aralıkta Dynatrace verisi yok",
  yMax,
  refLines,
  unit,
  minPoints = 2,
  step,
}: {
  series: SeriesDef[];
  format: (v: number | null) => string;
  height?: number;
  emptyText?: string;
  yMax?: number;
  refLines?: RefLine[];
  /** eksen birimi (ör. "ms", "%", "istek/dk") — eksenin üstünde gösterilir */
  unit?: string;
  minPoints?: number;
  /** ayrık ölçümler (ör. 15 dk'lık risk score) için basamaklı çizgi */
  step?: boolean;
}) {
  const ordered = useMemo(
    () => [...series].sort((a, b) => rank(a) - rank(b)),
    [series],
  );
  const rows = useMemo(() => {
    const m = new Map<string, Record<string, number | null | string>>();
    for (const s of ordered)
      for (const p of s.points) {
        const r = m.get(p.t) ?? { t: p.t };
        r[s.id] = p.v;
        m.set(p.t, r);
      }
    return [...m.values()].sort((a, b) => String(a.t).localeCompare(String(b.t)));
  }, [ordered]);
  const nPoints = rows.filter((r) => ordered.some((s) => !s.baseline && typeof r[s.id] === "number")).length;

  if (nPoints === 0 || nPoints < minPoints)
    return (
      <div style={{ height }} className="flex flex-col items-center justify-center gap-1 rounded-md border border-dashed border-line text-xs text-muted">
        <CircleHelp className="size-4" />
        {nPoints === 0 ? emptyText : `Yetersiz veri: ${nPoints} ölçüm (grafik için en az ${minPoints})`}
      </div>
    );

  const multi = ordered.length > 1;
  const colorOf = (s: SeriesDef) => (s.baseline ? CHART.baseline : (s.color ?? appColor(s.id)));
  const labelOf = (s: SeriesDef) => s.label ?? appLabel(s.id);
  return (
    <div>
      {(multi || unit) && (
        <div className="mb-1.5 flex items-center justify-between gap-3">
          {multi ? (
            <ul className="flex flex-wrap gap-x-3.5 gap-y-1" aria-label="lejant">
              {ordered.map((s) => (
                <li key={s.id} className="inline-flex items-center gap-1.5 text-xs text-ink-2">
                  <svg aria-hidden width="14" height="6" className="shrink-0">
                    <line x1="0" y1="3" x2="14" y2="3" stroke={colorOf(s)} strokeWidth="2" strokeDasharray={s.baseline ? "3 2" : undefined} />
                  </svg>
                  {labelOf(s)}
                </li>
              ))}
            </ul>
          ) : (
            <span />
          )}
          {unit && <span className="shrink-0 text-2xs text-muted">{unit}</span>}
        </div>
      )}
      <div style={{ height }} role="img" aria-label={`zaman serisi: ${ordered.map(labelOf).join(", ")}`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={CHART.grid} vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={(t) => fmtTime(String(t), false)}
              stroke={CHART.axis}
              tick={{ fill: CHART.muted, fontSize: 11 }}
              tickLine={false}
              minTickGap={48}
            />
            <YAxis
              stroke={CHART.axis}
              tick={{ fill: CHART.muted, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={52}
              tickFormatter={(v) => format(Number(v))}
              domain={[0, yMax ?? "auto"]}
              allowDataOverflow={yMax !== undefined}
            />
            {refLines?.map((r) => (
              <ReferenceLine
                key={r.y}
                y={r.y}
                stroke={CHART.axis}
                strokeDasharray="2 3"
                label={{ value: r.label, position: "insideTopRight", fill: CHART.muted, fontSize: 10 }}
              />
            ))}
            <Tooltip
              cursor={{ stroke: CHART.muted, strokeDasharray: "3 3" }}
              isAnimationActive={false}
              content={(p) => <ChartTooltip active={p.active} payload={p.payload} label={p.label} series={ordered} format={format} colorOf={colorOf} labelOf={labelOf} />}
            />
            {ordered.map((s) => (
              <Line
                key={s.id}
                dataKey={s.id}
                name={labelOf(s)}
                type={step ? "stepAfter" : "monotone"}
                stroke={colorOf(s)}
                strokeWidth={s.baseline ? 1.5 : 1.75}
                strokeDasharray={s.baseline ? "4 3" : undefined}
                dot={nPoints <= 3 ? { r: 2.5, strokeWidth: 0, fill: colorOf(s) } : false}
                activeDot={{ r: 3.5, stroke: CHART.surface, strokeWidth: 2 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function rank(s: SeriesDef) {
  if (s.baseline) return 1000;
  const i = APP_ORDER.indexOf(s.id);
  return i < 0 ? 99 : i;
}

interface TipProps {
  active?: boolean;
  payload?: readonly { dataKey?: unknown; value?: unknown }[];
  label?: unknown;
  series: SeriesDef[];
  format: (v: number | null) => string;
  colorOf: (s: SeriesDef) => string;
  labelOf: (s: SeriesDef) => string;
}

function ChartTooltip({ active, payload, label, series, format, colorOf, labelOf }: TipProps) {
  if (!active || !payload?.length) return null;
  const byKey = new Map(payload.map((p) => [String(p.dataKey), typeof p.value === "number" ? p.value : null]));
  return (
    <div className="min-w-40 rounded-md border border-line-strong bg-surface px-2.5 py-2 text-xs shadow-[var(--shadow-pop)]">
      <div className="mb-1 text-muted">{fmtTime(String(label))}</div>
      {series.map((s) => (
        <div key={s.id} className="flex items-center justify-between gap-4 py-px text-ink-2">
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className="h-0.5 w-2.5 rounded" style={{ background: colorOf(s) }} />
            {labelOf(s)}
          </span>
          <span className="font-medium text-ink tabular-nums">{byKey.has(s.id) ? format(byKey.get(s.id) ?? null) : "—"}</span>
        </div>
      ))}
    </div>
  );
}

/** Satır içi trend (tablolar için). Eksen yok; yalnız şekil + son değer tabloda yazılıdır. Boşluklar korunur. */
export function Sparkline({ values, color, width = 72, height = 20, label }: {
  values: (number | null)[];
  color?: string;
  width?: number;
  height?: number;
  label: string;
}) {
  const nums = values.filter((v): v is number => typeof v === "number");
  if (nums.length < 2) return <span className="text-2xs text-muted">—</span>;
  const max = Math.max(...nums);
  const min = Math.min(0, ...nums);
  const span = max - min || 1;
  const n = values.length;
  const x = (i: number) => (n === 1 ? width / 2 : (i / (n - 1)) * (width - 2) + 1);
  const y = (v: number) => height - 1 - ((v - min) / span) * (height - 2);
  const segs: string[] = [];
  let cur = "";
  values.forEach((v, i) => {
    if (typeof v !== "number") {
      if (cur) segs.push(cur);
      cur = "";
      return;
    }
    cur += `${cur ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  if (cur) segs.push(cur);
  return (
    <svg width={width} height={height} role="img" aria-label={label} className="block overflow-visible">
      {segs.map((d, i) => (
        <path key={i} d={d} fill="none" stroke={color ?? CHART.single} strokeWidth={1.25} strokeLinejoin="round" strokeLinecap="round" />
      ))}
    </svg>
  );
}

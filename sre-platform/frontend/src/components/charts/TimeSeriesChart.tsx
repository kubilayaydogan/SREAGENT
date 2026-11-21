import { CircleHelp } from "lucide-react";
import { useMemo } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { appColor, appLabel, APP_ORDER, CHART } from "@/lib/colors";
import { fmtTime } from "@/lib/utils";

export interface SeriesDef {
  id: string;
  label?: string;
  color?: string;
  points: { t: string; v: number | null }[];
}

/** Çok serili zaman serisi: TEK y ekseni, 2px çizgi, eksik nokta = boşluk (asla 0 değil), lejant + crosshair tooltip. */
export function TimeSeriesChart({ series, format, height = 220, emptyText = "Bu aralıkta Dynatrace verisi yok", yMax }: {
  series: SeriesDef[];
  format: (v: number | null) => string;
  height?: number;
  emptyText?: string;
  yMax?: number;
}) {
  const ordered = useMemo(
    () => [...series].sort((a, b) => (APP_ORDER.indexOf(a.id) + 99) % 99 - (APP_ORDER.indexOf(b.id) + 99) % 99),
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
  const hasData = rows.some((r) => ordered.some((s) => typeof r[s.id] === "number"));

  if (!hasData)
    return (
      <div style={{ height }} className="flex flex-col items-center justify-center gap-1.5 rounded-md border border-dashed border-line text-xs text-muted">
        <CircleHelp className="size-5" />
        {emptyText}
      </div>
    );

  const multi = ordered.length > 1;
  return (
    <div>
      {multi && (
        <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1" aria-label="lejant">
          {ordered.map((s) => (
            <li key={s.id} className="inline-flex items-center gap-1.5 text-xs text-ink-2">
              <span aria-hidden className="h-0.5 w-3 rounded" style={{ background: s.color ?? appColor(s.id) }} />
              {s.label ?? appLabel(s.id)}
            </li>
          ))}
        </ul>
      )}
      <div style={{ height }} role="img" aria-label={`zaman serisi: ${ordered.map((s) => s.label ?? appLabel(s.id)).join(", ")}`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={CHART.grid} vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={(t) => fmtTime(String(t), false)}
              stroke={CHART.axis}
              tick={{ fill: CHART.muted, fontSize: 11 }}
              tickLine={false}
              minTickGap={40}
            />
            <YAxis
              stroke={CHART.axis}
              tick={{ fill: CHART.muted, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={58}
              tickFormatter={(v) => format(Number(v))}
              domain={[0, yMax ?? "auto"]}
            />
            <Tooltip
              cursor={{ stroke: CHART.muted, strokeDasharray: "3 3" }}
              content={(p) => <ChartTooltip active={p.active} payload={p.payload} label={p.label} series={ordered} format={format} />}
            />
            {ordered.map((s) => (
              <Line
                key={s.id}
                dataKey={s.id}
                name={s.label ?? appLabel(s.id)}
                type="monotone"
                stroke={s.color ?? appColor(s.id)}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, stroke: CHART.surface, strokeWidth: 2 }}
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

interface TipProps {
  active?: boolean;
  payload?: readonly { dataKey?: unknown; value?: unknown }[];
  label?: unknown;
  series: SeriesDef[];
  format: (v: number | null) => string;
}

function ChartTooltip({ active, payload, label, series, format }: TipProps) {
  if (!active || !payload?.length) return null;
  const byKey = new Map(payload.map((p) => [String(p.dataKey), typeof p.value === "number" ? p.value : null]));
  return (
    <div className="rounded-md border border-line-strong bg-surface-2 px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-medium text-ink">{fmtTime(String(label))}</div>
      {series.map((s) => (
        <div key={s.id} className="flex items-center justify-between gap-4 text-ink-2">
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className="size-2 rounded-full" style={{ background: s.color ?? appColor(s.id) }} />
            {s.label ?? appLabel(s.id)}
          </span>
          <span className="font-mono text-ink">{byKey.has(s.id) ? format(byKey.get(s.id) ?? null) : "—"}</span>
        </div>
      ))}
    </div>
  );
}

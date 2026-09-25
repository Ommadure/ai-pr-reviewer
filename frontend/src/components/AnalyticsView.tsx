import { motion, useReducedMotion } from "motion/react";
import { useCallback, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import type { Analytics, Rate } from "../api/types";
import { count, humanize, money, percent } from "../lib/format";
import { EASE_OUT } from "../lib/motion";
import { spotlight } from "../lib/spotlight";
import { DialGauge, LatencyTape, Readout } from "./cockpit/Instruments";
import { Empty } from "./States";

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];
const SEVERITY_COLOR: Record<string, string> = {
  critical: "var(--red)",
  high: "var(--amber)",
  medium: "color-mix(in srgb, var(--amber) 60%, transparent)",
  low: "var(--cyan)",
  info: "var(--muted)",
};

// Stable formatters: the rolling readouts restart if these change identity.
const whole = (n: number) => count(Math.round(n));

/** A panel on the instrument board. It catches the light where the pointer is. */
function Panel({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section
      onPointerMove={spotlight}
      className={`spotlight rounded-[8px] border border-line bg-surface p-5 shadow-panel ${className}`}
      aria-label={title}
    >
      <h2 className="placard mb-4">{title}</h2>
      {children}
    </section>
  );
}

/**
 * The primary flight display for reviews: helpfulness on a dial, volume and cost as
 * rolling readouts, and review time on a tape.
 */
export function InstrumentCluster({ data }: { data: Analytics }) {
  const rupees = data.usd_to_inr;
  const costFormat = useCallback((n: number) => money(n, rupees), [rupees]);
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,17rem)_1fr]">
      <Panel title="Helpful rate" className="flex flex-col justify-center">
        <DialGauge value={data.feedback.helpful_rate} label="comments that helped">
          {data.feedback.posted
            ? `👍 or fixed, of ${count(data.feedback.posted)} posted · 👎 ${percent(data.feedback.negative_rate)}`
            : "No feedback yet: it appears as people react to comments."}
        </DialGauge>
      </Panel>
      <div className="grid gap-3">
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="reviews">
            <Readout value={data.runs_completed} format={whole} />
            <StatNote warn={data.runs_failed > 0}>{data.runs_failed ? `${count(data.runs_failed)} failed` : "none failed"}</StatNote>
          </Stat>
          <Stat label="comments posted">
            <Readout value={data.comments_posted} format={whole} />
            <StatNote>{`${count(data.runs_total)} runs in ${data.range_days} days`}</StatNote>
          </Stat>
          <Stat label={`cost, ${data.range_days} days`} className="col-span-2 sm:col-span-1">
            <Readout value={data.cost_usd_total} format={costFormat} />
            <StatNote>{data.cost_usd_total === 0 ? "free tier, or no price set" : "LLM tokens"}</StatNote>
          </Stat>
        </dl>
        <Panel title="Review time">
          <LatencyTape avg={data.avg_latency_ms} p95={data.p95_latency_ms} />
        </Panel>
      </div>
    </div>
  );
}

function Stat({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div onPointerMove={spotlight} className={`spotlight rounded-[8px] border border-line bg-surface px-5 py-4 shadow-panel ${className}`}>
      <dt className="placard">{label}</dt>
      <dd className="mt-3 font-mono text-[28px] leading-none font-bold text-ink">{children}</dd>
    </div>
  );
}

function StatNote({ children, warn = false }: { children: ReactNode; warn?: boolean }) {
  return <span className={`mt-2 block font-sans text-xs font-normal ${warn ? "text-amber" : "text-muted"}`}>{children}</span>;
}

export function AnalyticsView({ data }: { data: Analytics }) {
  const reduced = useReducedMotion();
  if (data.runs_total === 0) {
    return (
      <Empty title="No reviews in this period">
        Open or update a pull request on a repository where ReviewPilot is installed, and its review shows up here.
      </Empty>
    );
  }
  const rate = data.usd_to_inr ?? 1;
  const perDay = data.reviews_per_day.map((d, i) => ({
    day: d.day.slice(5),
    reviews: d.count,
    cost: (data.cost_per_day[i]?.cost_usd ?? 0) * rate,
  }));
  const bySeverity = SEVERITY_ORDER.filter((s) => data.comments_by_severity[s]).map((s) => ({
    name: s,
    comments: data.comments_by_severity[s] ?? 0,
  }));
  const byCategory = Object.entries(data.comments_by_category)
    .sort((a, b) => b[1] - a[1])
    .map(([name, comments]) => ({ name: humanize(name), comments }));
  const unit = data.usd_to_inr ? "₹" : "$";

  return (
    <div className="space-y-3">
      <InstrumentCluster data={data} />
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Reviews per day">
          <ChartBox>
            <BarChart data={perDay} margin={{ top: 4, right: 4, left: -8, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="2 4" />
              <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={{ stroke: "var(--line-strong)" }} minTickGap={16} />
              <YAxis allowDecimals={false} tick={axis} tickLine={false} axisLine={false} width={28} />
              <Tooltip cursor={{ fill: "var(--route-soft)" }} content={(props) => <ChartTip {...props} unit="reviews" />} />
              <Bar dataKey="reviews" fill="var(--route)" radius={[2, 2, 0, 0]} maxBarSize={28} isAnimationActive={!reduced} animationDuration={700} />
            </BarChart>
          </ChartBox>
        </Panel>
        <Panel title={`Cost per day (${unit})`}>
          <ChartBox>
            <BarChart data={perDay} margin={{ top: 4, right: 4, left: -8, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="2 4" />
              <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={{ stroke: "var(--line-strong)" }} minTickGap={16} />
              <YAxis tick={axis} tickLine={false} axisLine={false} width={40} />
              <Tooltip cursor={{ fill: "var(--cyan-soft)" }} content={(props) => <ChartTip {...props} unit={unit} money />} />
              <Bar dataKey="cost" fill="var(--cyan)" radius={[2, 2, 0, 0]} maxBarSize={28} isAnimationActive={!reduced} animationDuration={700} />
            </BarChart>
          </ChartBox>
          {data.cost_usd_total === 0 ? (
            <p className="mt-2 text-xs text-muted">No cost recorded: the model is on a free tier, or LLM_PRICING has no price for it.</p>
          ) : null}
        </Panel>
        <Panel title="Comments by severity">
          <HorizontalBars rows={bySeverity} colorFor={(name) => SEVERITY_COLOR[name] ?? "var(--muted)"} />
        </Panel>
        <Panel title="Comments by category">
          <HorizontalBars rows={byCategory} colorFor={() => "var(--route)"} />
        </Panel>
        <Panel title="Usefulness by category">
          <RateTable rates={data.feedback_by_category} />
        </Panel>
        <Panel title="Most flagged files">
          {data.top_files.length ? (
            <ol className="space-y-2 text-sm">
              {data.top_files.map((f, i) => (
                <li key={`${f.repository_full_name}/${f.path}`} className="flex items-baseline gap-3">
                  <span className="w-6 shrink-0 font-mono text-[11px] text-faint">{String(i + 1).padStart(2, "0")}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={`${f.repository_full_name}/${f.path}`}>
                    {f.path}
                  </span>
                  <span className="shrink-0 font-mono text-xs font-bold text-route tabular">{f.count}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted">No comments posted yet.</p>
          )}
        </Panel>
      </div>
    </div>
  );
}

const axis = { fontSize: 10, fill: "var(--faint)", fontFamily: "var(--font-mono)" };

/** Tooltip drawn as a small readout box instead of the library default. */
function ChartTip({ active, payload, label, unit, money: isMoney = false }: TooltipContentProps & { unit: string; money?: boolean }) {
  const value = payload?.[0]?.value;
  if (!active || value == null) return null;
  const n = Number(value);
  return (
    <div className="rounded-[4px] border border-line-strong bg-surface-3 px-3 py-2 font-mono text-xs shadow-panel">
      <p className="text-[10px] text-muted">{label}</p>
      <p className="mt-0.5 font-bold text-ink">{isMoney ? `${unit}${n.toFixed(n < 10 ? 3 : 0)}` : `${n} ${unit}`}</p>
    </div>
  );
}

function ChartBox({ children }: { children: React.ReactElement }) {
  return (
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

/** Plain CSS bars (readable by screen readers as a list) that fill in when they come into view. */
function HorizontalBars({ rows, colorFor }: { rows: { name: string; comments: number }[]; colorFor: (name: string) => string }) {
  const max = Math.max(1, ...rows.map((r) => r.comments));
  if (!rows.length) return <p className="text-sm text-muted">No comments posted yet.</p>;
  return (
    <ul className="space-y-2.5">
      {rows.map((r, i) => (
        <li key={r.name} className="grid grid-cols-[6.5rem_1fr_2.5rem] items-center gap-3 text-sm">
          <span className="truncate text-muted">{r.name}</span>
          <span className="h-2 overflow-hidden rounded-[2px] bg-surface-2">
            <motion.span
              className="block h-2 origin-left rounded-[2px]"
              style={{ width: `${(r.comments / max) * 100}%`, background: colorFor(r.name) }}
              initial={{ scaleX: 0 }}
              whileInView={{ scaleX: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, ease: EASE_OUT, delay: i * 0.06 }}
            />
          </span>
          <span className="text-right font-mono text-xs text-ink tabular">{r.comments}</span>
        </li>
      ))}
    </ul>
  );
}

function RateTable({ rates }: { rates: Record<string, Rate> }) {
  const rows = Object.entries(rates).sort((a, b) => b[1].posted - a[1].posted);
  if (!rows.length) return <p className="text-sm text-muted">No posted comments to rate yet.</p>;
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left">
          <th className="placard pb-2 !text-[10px] font-normal">category</th>
          <th className="placard pb-2 text-right !text-[10px] font-normal">posted</th>
          <th className="placard pb-2 text-right !text-[10px] font-normal">helpful</th>
          <th className="placard pb-2 text-right !text-[10px] font-normal">negative</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, r]) => (
          <tr key={name} className="border-t border-line">
            <td className="py-2">{humanize(name)}</td>
            <td className="py-2 text-right font-mono text-xs tabular">{r.posted}</td>
            <td className="py-2 text-right font-mono text-xs text-green tabular">{percent(r.helpful_rate)}</td>
            <td className="py-2 text-right font-mono text-xs text-red tabular">{percent(r.negative_rate)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

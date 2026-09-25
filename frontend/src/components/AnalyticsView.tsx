import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Analytics, Rate } from "../api/types";
import { count, duration, humanize, money, percent } from "../lib/format";
import { Empty } from "./States";

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

export function KpiStrip({ data }: { data: Analytics }) {
  const items = [
    { label: "reviews", value: count(data.runs_completed), note: `${count(data.runs_failed)} failed` },
    { label: "comments posted", value: count(data.comments_posted) },
    { label: "helpful rate", value: percent(data.feedback.helpful_rate), note: `of ${count(data.feedback.posted)} posted` },
    { label: `cost, ${data.range_days} days`, value: money(data.cost_usd_total, data.usd_to_inr) },
    { label: "p95 latency", value: duration(data.p95_latency_ms), note: `avg ${duration(data.avg_latency_ms)}` },
  ];
  return (
    // Flex-wrap instead of a grid: tiles in a short last row stretch, so no empty cells.
    <dl className="flex flex-wrap overflow-hidden rounded-lg border border-line bg-line" style={{ gap: "1px" }}>
      {items.map((item) => (
        <div key={item.label} className="min-w-36 grow basis-36 bg-surface px-4 py-3">
          <dt className="font-mono text-[11px] uppercase tracking-wide text-muted">{item.label}</dt>
          <dd className="mt-1 font-display text-2xl font-bold tabular text-ink">{item.value}</dd>
          {item.note ? <dd className="text-xs text-muted">{item.note}</dd> : null}
        </div>
      ))}
    </dl>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-surface p-4" aria-label={title}>
      <h2 className="mb-3 font-mono text-xs uppercase tracking-wide text-muted">{title}</h2>
      {children}
    </section>
  );
}

const axis = { fontSize: 11, fill: "var(--muted)", fontFamily: "var(--font-mono)" };
const tooltip = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--ink)" },
  cursor: { fill: "var(--gutter)" },
};

export function AnalyticsView({ data }: { data: Analytics }) {
  if (data.runs_total === 0) {
    return (
      <Empty title="No reviews in this period">
        Open or update a pull request on a repository where ReviewPilot is installed, and its review shows up here.
      </Empty>
    );
  }
  const perDay = data.reviews_per_day.map((d, i) => ({
    day: d.day.slice(5),
    reviews: d.count,
    cost: data.cost_per_day[i]?.cost_usd ?? 0,
  }));
  const bySeverity = SEVERITY_ORDER.filter((s) => data.comments_by_severity[s]).map((s) => ({
    name: s,
    comments: data.comments_by_severity[s] ?? 0,
  }));
  const byCategory = Object.entries(data.comments_by_category)
    .sort((a, b) => b[1] - a[1])
    .map(([name, comments]) => ({ name: humanize(name), comments }));

  return (
    <div className="space-y-4">
      <KpiStrip data={data} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Reviews per day">
          <ChartBox>
            <BarChart data={perDay}>
              <CartesianGrid vertical={false} stroke="var(--line)" />
              <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={false} />
              <YAxis allowDecimals={false} tick={axis} tickLine={false} axisLine={false} width={28} />
              <Tooltip {...tooltip} />
              <Bar dataKey="reviews" fill="var(--accent)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ChartBox>
        </Panel>
        <Panel title={`Cost per day (${data.usd_to_inr ? "₹" : "$"})`}>
          <ChartBox>
            <BarChart data={perDay.map((d) => ({ ...d, cost: d.cost * (data.usd_to_inr ?? 1) }))}>
              <CartesianGrid vertical={false} stroke="var(--line)" />
              <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={false} />
              <YAxis tick={axis} tickLine={false} axisLine={false} width={40} />
              <Tooltip {...tooltip} formatter={(v) => Number(v).toFixed(3)} />
              <Bar dataKey="cost" fill="var(--sev-low)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ChartBox>
          {data.cost_usd_total === 0 ? (
            <p className="mt-2 text-xs text-muted">
              No cost recorded: the model is on a free tier, or LLM_PRICING has no price for it.
            </p>
          ) : null}
        </Panel>
        <Panel title="Comments by severity">
          <HorizontalBars rows={bySeverity} colorFor={(name) => `var(--sev-${name})`} />
        </Panel>
        <Panel title="Comments by category">
          <HorizontalBars rows={byCategory} colorFor={() => "var(--accent)"} />
        </Panel>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Usefulness by category">
          <RateTable rates={data.feedback_by_category} />
        </Panel>
        <Panel title="Most flagged files">
          {data.top_files.length ? (
            <ol className="space-y-1.5 text-sm">
              {data.top_files.map((f) => (
                <li key={`${f.repository_full_name}/${f.path}`} className="flex items-baseline gap-3">
                  <span className="w-8 shrink-0 text-right font-mono text-xs text-muted tabular">{f.count}</span>
                  <span className="min-w-0 truncate font-mono text-xs" title={`${f.repository_full_name}/${f.path}`}>
                    {f.path}
                  </span>
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

function ChartBox({ children }: { children: React.ReactElement }) {
  return (
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

/** Plain CSS bars: readable by screen readers as a list, and they need no chart library. */
function HorizontalBars({ rows, colorFor }: { rows: { name: string; comments: number }[]; colorFor: (name: string) => string }) {
  const max = Math.max(1, ...rows.map((r) => r.comments));
  if (!rows.length) return <p className="text-sm text-muted">No comments posted yet.</p>;
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.name} className="grid grid-cols-[6.5rem_1fr_2.5rem] items-center gap-3 text-sm">
          <span className="truncate text-muted">{r.name}</span>
          <span className="h-2 rounded-full bg-gutter">
            <span className="block h-2 rounded-full" style={{ width: `${(r.comments / max) * 100}%`, background: colorFor(r.name) }} />
          </span>
          <span className="text-right font-mono text-xs tabular">{r.comments}</span>
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
        <tr className="text-left font-mono text-[11px] uppercase tracking-wide text-muted">
          <th className="pb-2 font-normal">category</th>
          <th className="pb-2 text-right font-normal">posted</th>
          <th className="pb-2 text-right font-normal">helpful</th>
          <th className="pb-2 text-right font-normal">negative</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, r]) => (
          <tr key={name} className="border-t border-line">
            <td className="py-1.5">{humanize(name)}</td>
            <td className="py-1.5 text-right font-mono tabular">{r.posted}</td>
            <td className="py-1.5 text-right font-mono tabular text-add-ink">{percent(r.helpful_rate)}</td>
            <td className="py-1.5 text-right font-mono tabular text-del-ink">{percent(r.negative_rate)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

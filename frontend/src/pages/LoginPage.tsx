import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Navigate, useSearchParams } from "react-router";
import { LOGIN_URL } from "../api/client";
import { useMe } from "../api/hooks";
import { SeverityBadge } from "../components/Badges";
import { RadarScope, type Contact } from "../components/cockpit/RadarScope";
import { Logo } from "../components/Logo";
import { ThemeToggle } from "../components/ThemeToggle";
import { button } from "../components/ui";
import { DURATION, EASE_OUT, rise } from "../lib/motion";

const APP_SLUG = import.meta.env.VITE_GITHUB_APP_SLUG ?? "reviewpilot-om";
const INSTALL_URL = `https://github.com/apps/${APP_SLUG}/installations/new`;

const ERRORS: Record<string, string> = {
  denied: "GitHub sign-in was cancelled. Sign in again when you're ready.",
  state: "That sign-in link expired or didn't start here. Start again from this page.",
  github: "GitHub didn't complete the sign-in. Try again in a moment.",
};

type Line = { n: number | null; kind: "ctx" | "add" | "del"; code: string };
type Finding = Contact & { file: string; line: number; lines: Line[]; body: string };

// Three findings of the kind ReviewPilot posts, each pinned to the changed line.
const SQL: Finding = {
  id: "sql",
  bearing: 48,
  range: 0.62,
  severity: "critical",
  label: "SQL built from user input",
  file: "app/users.py",
  line: 12,
  lines: [
    { n: 11, kind: "ctx", code: "def get_user(db, uid):" },
    { n: null, kind: "del", code: '    q = "SELECT * FROM users WHERE id = ?"' },
    { n: 12, kind: "add", code: '    q = f"SELECT * FROM users WHERE id = {uid}"' },
  ],
  body: "`uid` goes straight into the SQL string. Pass it as a query parameter instead.",
};
const FINDINGS: Finding[] = [
  SQL,
  {
    id: "await",
    bearing: 168,
    range: 0.82,
    severity: "high",
    label: "Response checked before it arrives",
    file: "web/api/payments.ts",
    line: 48,
    lines: [
      { n: 47, kind: "ctx", code: "export async function charge(url: string) {" },
      { n: 48, kind: "add", code: "  const res = fetch(url, { method: 'POST' });" },
      { n: 49, kind: "add", code: "  if (!res.ok) throw new PaymentError();" },
    ],
    body: "`fetch` returns a promise, so `res.ok` is always undefined here. Add `await`.",
  },
  {
    id: "loop",
    bearing: 292,
    range: 0.44,
    severity: "medium",
    label: "Config reloaded for every row",
    file: "jobs/export.py",
    line: 31,
    lines: [
      { n: 30, kind: "ctx", code: "for row in rows:" },
      { n: 31, kind: "add", code: "    cfg = load_config()" },
      { n: 32, kind: "ctx", code: "    writer.write(format_row(row, cfg))" },
    ],
    body: "`load_config()` reads the file once per row. Load it once, before the loop.",
  },
];

const PERIOD = 6; // seconds per sweep

// The readout changing to another finding.
const swap = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0, transition: { duration: DURATION.base, ease: EASE_OUT } },
  exit: { opacity: 0, y: -4, transition: { duration: DURATION.fast } },
};

/** Which finding the sweep most recently passed, driven by the same clock as the CSS sweep. */
function useSweepFocus(held: boolean) {
  const [angle, setAngle] = useState(0);
  const elapsed = useRef(0);
  useEffect(() => {
    if (held) return;
    let last = performance.now();
    const timer = setInterval(() => {
      const now = performance.now();
      elapsed.current += now - last;
      last = now;
      setAngle(((elapsed.current / 1000) % PERIOD) * (360 / PERIOD));
    }, 150);
    return () => clearInterval(timer);
  }, [held]);
  const passed = [...FINDINGS].reverse().find((f) => f.bearing <= angle);
  return (passed ?? FINDINGS.at(-1) ?? SQL).id;
}

export function LoginPage() {
  const me = useMe();
  const [params] = useSearchParams();
  const error = ERRORS[params.get("error") ?? ""];
  if (me.isSuccess) return <Navigate to="/" replace />;

  return (
    <div className="min-h-screen overflow-x-clip">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-8">
        <Logo />
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <a href={LOGIN_URL} className={`${button.ghost} hidden sm:inline-flex`}>
            Sign in
          </a>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 pt-8 pb-20 sm:px-8 lg:grid-cols-[1fr_1.15fr] lg:gap-12 lg:pt-16">
          <motion.div initial="hidden" animate="show" transition={{ staggerChildren: 0.08 }}>
            <motion.p variants={rise} className="placard flex items-center gap-2">
              <span aria-hidden className="h-px w-6 bg-route" />
              AI code review · GitHub App
            </motion.p>
            <motion.h1
              variants={rise}
              className="mt-5 font-display text-[clamp(2.5rem,1.6rem+3.6vw,4.4rem)] leading-[0.98] font-bold tracking-[-0.02em] text-balance"
            >
              Every pull request gets a <span className="whitespace-nowrap text-route">pre‑flight check</span>.
            </motion.h1>
            <motion.p variants={rise} className="mt-6 max-w-[34rem] text-lg leading-relaxed text-muted">
              ReviewPilot reads each change as soon as it's pushed, flags what could break on the exact line, and keeps a
              log of what every review cost and whether it helped.
            </motion.p>
            {error ? (
              <p role="alert" className="mt-6 flex items-center gap-3 rounded-[6px] border border-red/40 bg-red-soft px-4 py-3 text-sm text-ink">
                <span className="rounded-[3px] bg-red px-1.5 py-0.5 font-mono text-[10px] font-bold text-on-route">CAUTION</span>
                {error}
              </p>
            ) : null}
            <motion.div variants={rise} className="mt-8 flex flex-wrap gap-3">
              <a href={LOGIN_URL} className={`${button.primary} min-h-11 px-6`}>
                <GitHubMark />
                Sign in with GitHub
              </a>
              <a href={INSTALL_URL} className={`${button.secondary} min-h-11 px-6`}>
                Install on GitHub
              </a>
            </motion.div>
            <motion.ul variants={rise} className="mt-10 grid gap-2 text-sm text-muted sm:grid-cols-3" aria-label="How it behaves">
              {["Comments only on changed lines", "One review per push", "Cost and usefulness logged"].map((item) => (
                <li key={item} className="flex items-center gap-2">
                  <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-green shadow-[0_0_8px_var(--green)]" />
                  {item}
                </li>
              ))}
            </motion.ul>
          </motion.div>

          <ScanDemo />
        </section>

        <FlightPlan />
      </main>

      <footer className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-6 font-mono text-[11px] text-faint sm:px-8">
        <span>ReviewPilot · reviews you can measure</span>
        <span>FastAPI · Celery · Postgres · React</span>
      </footer>
    </div>
  );
}

/** The hero instrument: the scope finds the issues, the readout shows the one under the sweep. */
function ScanDemo() {
  const reduced = useReducedMotion();
  const [held, setHeld] = useState(Boolean(reduced));
  const [picked, setPicked] = useState<string | null>(null);
  const swept = useSweepFocus(held);
  const activeId = picked ?? swept;
  const finding = FINDINGS.find((f) => f.id === activeId) ?? SQL;

  const pick = (id: string) => {
    setPicked(id);
    setHeld(true);
  };

  return (
    <motion.figure
      aria-label="Example: a ReviewPilot comment on a changed line"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DURATION.slower, ease: EASE_OUT, delay: 0.2 }}
      className="relative overflow-hidden rounded-[10px] border border-line-strong bg-surface shadow-panel"
    >
      <figcaption className="flex items-center justify-between gap-3 border-b border-line bg-surface-2 px-4 py-2.5">
        <span className="placard flex items-center gap-2 !text-[10px]">
          <span aria-hidden className={`size-1.5 rounded-full ${held ? "bg-amber" : "bg-green shadow-[0_0_8px_var(--green)]"}`} />
          {held ? "Scan held" : "Scanning pull request #42"}
        </span>
        <button
          type="button"
          aria-pressed={held}
          onClick={() => {
            setHeld((h) => !h);
            setPicked(null);
          }}
          className="rounded-[3px] border border-line-strong px-2 py-1 font-mono text-[10px] font-bold tracking-[0.08em] text-muted transition-colors hover:border-cyan hover:text-cyan"
        >
          {held ? "RESUME" : "HOLD"}
        </button>
      </figcaption>

      <div className="grid sm:grid-cols-[minmax(0,14rem)_1fr]">
        <div className="flex items-center justify-center border-b border-line p-6 sm:border-r sm:border-b-0 sm:p-5">
          <RadarScope
            label="Findings on the scope; choose one to read it"
            contacts={FINDINGS}
            activeId={activeId}
            onSelect={pick}
            period={PERIOD}
            paused={held}
            className="w-full max-w-[13rem]"
          />
        </div>
        <div className="min-w-0 p-5" aria-live={held ? "polite" : "off"}>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={finding.id} {...swap}>
              <p className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={finding.severity} />
                <span className="font-mono text-[11px] text-muted">
                  {finding.file}:{finding.line}
                </span>
              </p>
              <p className="mt-3 font-display text-xl leading-snug font-semibold">{finding.label}</p>
              <p className="mt-3 border-l-2 border-route pl-3 text-sm leading-relaxed text-muted">
                <span className="font-medium text-route">reviewpilot</span> · <Ticked text={finding.body} />
              </p>
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      {/* the changed lines, full width so code never has to scroll */}
      <div className="border-t border-line bg-bg/40 font-mono text-[12px] leading-6">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={finding.id} {...swap} className="overflow-x-auto py-2">
            {finding.lines.map((l, i) => (
              <div key={i} className={`flex ${l.kind === "add" ? "bg-green-soft" : l.kind === "del" ? "bg-red-soft" : ""}`}>
                <span className={`w-12 shrink-0 pr-3 text-right select-none ${l.n === finding.line ? "font-bold text-route" : "text-faint"}`}>
                  {l.n ?? ""}
                </span>
                <span className={`w-5 shrink-0 text-center select-none ${l.kind === "add" ? "text-green" : "text-red"}`}>
                  {l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}
                </span>
                <span className="pr-4 whitespace-pre">{l.code}</span>
              </div>
            ))}
          </motion.div>
        </AnimatePresence>
      </div>
    </motion.figure>
  );
}

/** `code` spans in the demo copy. */
function Ticked({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`]+`)/g).map((part, i) =>
        part.startsWith("`") ? (
          <code key={i} className="rounded-[3px] bg-surface-2 px-1 font-mono text-[0.85em] text-ink">
            {part.slice(1, -1)}
          </code>
        ) : (
          part
        ),
      )}
    </>
  );
}

const WAYPOINTS: { fix: string; title: string; body: string }[] = [
  { fix: "PUSH", title: "A pull request changes", body: "You open or update a pull request, and GitHub tells ReviewPilot within seconds." },
  { fix: "SCAN", title: "The diff is read", body: "Changes are split into reviewable chunks. Secrets are redacted before anything reaches the model." },
  { fix: "CHECK", title: "Every comment is verified", body: "Each comment must point at a line you actually changed. Anything off target is dropped." },
  { fix: "POST", title: "One review lands", body: "A single review with suggested fixes, plus a check run with the result." },
  { fix: "LEARN", title: "Feedback is counted", body: "👍, 👎 and fixed-in-a-later-push are tracked, so you can see which comments help." },
];

/**
 * How a review travels, drawn as a flight plan: the magenta route line connects the
 * waypoints in order, and it draws itself in as the section scrolls into view.
 */
function FlightPlan() {
  const [focus, setFocus] = useState<number | null>(null);
  return (
    <section aria-labelledby="route-title" className="border-t border-line bg-surface/50">
      <div className="mx-auto max-w-6xl px-4 py-20 sm:px-8">
        <p className="placard flex items-center gap-2">
          <span aria-hidden className="h-px w-6 bg-route" />
          Flight plan
        </p>
        <h2 id="route-title" className="mt-4 max-w-xl font-display text-3xl leading-tight font-bold tracking-tight sm:text-4xl">
          From push to review, in five waypoints.
        </h2>

        <ol className="relative mt-14 grid gap-10 lg:grid-cols-5 lg:gap-6">
          {/* the route line: vertical on phones, horizontal on wide screens */}
          <motion.span
            aria-hidden
            className="absolute top-2 bottom-2 left-[7px] w-0.5 origin-top bg-route lg:top-[7px] lg:right-[10%] lg:bottom-auto lg:left-[10%] lg:h-0.5 lg:w-auto lg:origin-left"
            initial={{ scaleY: 0, scaleX: 0 }}
            whileInView={{ scaleY: 1, scaleX: 1 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 1.2, ease: EASE_OUT }}
          />
          {WAYPOINTS.map((w, i) => {
            const dim = focus !== null && focus !== i;
            return (
              <motion.li
                key={w.fix}
                className="relative pl-9 lg:pl-0 lg:text-center"
                initial={{ opacity: 0, y: 10 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, amount: 0.4 }}
                transition={{ duration: DURATION.slow, ease: EASE_OUT, delay: 0.15 + i * 0.12 }}
                onPointerEnter={() => setFocus(i)}
                onPointerLeave={() => setFocus(null)}
              >
                <span
                  aria-hidden
                  className={`absolute top-0 left-0 grid size-4 rotate-45 place-items-center border-2 bg-bg transition-colors duration-200 lg:relative lg:mx-auto ${
                    focus === i ? "border-cyan" : "border-route"
                  }`}
                >
                  <span className={`size-1 ${focus === i ? "bg-cyan" : "bg-route"}`} />
                </span>
                <div className={`transition-opacity duration-200 ${dim ? "opacity-40" : ""}`}>
                  <p className={`mt-0 font-mono text-xs font-bold tracking-[0.12em] lg:mt-5 ${focus === i ? "text-cyan" : "text-route"}`}>
                    {String(i + 1).padStart(2, "0")} {w.fix}
                  </p>
                  <h3 className="mt-2 font-display text-lg font-semibold">{w.title}</h3>
                  <p className="mx-auto mt-1.5 max-w-xs text-sm leading-relaxed text-muted">{w.body}</p>
                </div>
              </motion.li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}

function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" className="size-4" fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

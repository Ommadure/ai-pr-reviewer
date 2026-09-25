import { Navigate, useSearchParams } from "react-router";
import { LOGIN_URL } from "../api/client";
import { useMe } from "../api/hooks";
import { ThemeToggle } from "../components/ThemeToggle";

const APP_SLUG = import.meta.env.VITE_GITHUB_APP_SLUG ?? "reviewpilot-om";
const INSTALL_URL = `https://github.com/apps/${APP_SLUG}/installations/new`;

const ERRORS: Record<string, string> = {
  denied: "GitHub sign-in was cancelled. Sign in again when you're ready.",
  state: "That sign-in link expired or didn't start here. Start again from this page.",
  github: "GitHub didn't complete the sign-in. Try again in a moment.",
};

// The page's thesis: a real diff, and a review comment on the exact line.
const DIFF: { n: number | null; kind: "ctx" | "add" | "del"; code: string }[] = [
  { n: 11, kind: "ctx", code: "def get_user(db, uid):" },
  { n: null, kind: "del", code: '    q = "SELECT * FROM users WHERE id = ?"' },
  { n: 12, kind: "add", code: '    q = f"SELECT * FROM users WHERE id = {uid}"' },
  { n: 13, kind: "ctx", code: "    return db.execute(q).fetchone()" },
];

export function LoginPage() {
  const me = useMe();
  const [params] = useSearchParams();
  const error = ERRORS[params.get("error") ?? ""];
  if (me.isSuccess) return <Navigate to="/" replace />;

  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-8">
        <span className="font-display text-lg font-bold tracking-tight">
          Review<span className="text-accent">Pilot</span>
        </span>
        <ThemeToggle />
      </header>
      <main className="mx-auto grid max-w-6xl items-center gap-10 px-4 pt-6 pb-16 sm:px-8 lg:grid-cols-[1fr_1.15fr] lg:pt-16">
        <section>
          <h1 className="font-display text-4xl font-bold leading-[1.05] tracking-tight sm:text-5xl">
            Pull request reviews that point at the exact line.
          </h1>
          <p className="mt-5 max-w-md text-lg leading-relaxed text-muted">
            ReviewPilot reads every pull request, comments only where the code changed, and shows you what
            each review cost and whether people found it useful.
          </p>
          {error ? (
            <p role="alert" className="mt-6 rounded-md bg-del px-4 py-3 text-sm text-del-ink">
              {error}
            </p>
          ) : null}
          <div className="mt-8 flex flex-wrap gap-3">
            <a href={LOGIN_URL} className="rounded-md bg-ink px-5 py-2.5 text-sm font-medium text-bg hover:opacity-90">
              Sign in with GitHub
            </a>
            <a
              href={INSTALL_URL}
              className="rounded-md border border-line bg-surface px-5 py-2.5 text-sm font-medium text-ink hover:bg-surface-2"
            >
              Install on GitHub
            </a>
          </div>
        </section>

        <figure aria-label="Example: a ReviewPilot comment on a changed line" className="overflow-hidden rounded-xl border border-line bg-surface shadow-sm">
          <figcaption className="flex items-center justify-between border-b border-line bg-surface-2 px-4 py-2 font-mono text-xs text-muted">
            <span>app/users.py</span>
            <span>
              <span className="text-add-ink">+1</span> <span className="text-del-ink">−1</span>
            </span>
          </figcaption>
          <div className="overflow-x-auto font-mono text-[13px] leading-6">
            {DIFF.map((line, i) => (
              <div key={i}>
                <div className={`flex ${line.kind === "add" ? "bg-add" : line.kind === "del" ? "bg-del" : ""}`}>
                  <span className="w-12 shrink-0 bg-gutter pr-3 text-right text-muted select-none">{line.n ?? ""}</span>
                  <span className={`w-5 shrink-0 text-center select-none ${line.kind === "add" ? "text-add-ink" : "text-del-ink"}`}>
                    {line.kind === "add" ? "+" : line.kind === "del" ? "−" : ""}
                  </span>
                  <span className="pr-4 whitespace-pre">{line.code}</span>
                </div>
                {line.kind === "add" ? (
                  <div className="attach flex bg-surface-2">
                    <span className="w-12 shrink-0 bg-gutter" />
                    <div className="m-3 flex-1 rounded-lg border border-line border-l-2 border-l-accent bg-surface p-3 font-sans">
                      <p className="text-xs text-muted">
                        <span className="font-medium text-accent">reviewpilot</span> · line 12
                      </p>
                      <p className="mt-1 text-sm font-medium">
                        <span className="font-mono text-[11px] uppercase text-sev-critical">critical</span> SQL built
                        from user input
                      </p>
                      <p className="mt-1 text-sm text-muted">
                        <code className="font-mono text-xs">uid</code> goes straight into the SQL string. Pass it as a
                        query parameter instead.
                      </p>
                    </div>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </figure>
      </main>
    </div>
  );
}

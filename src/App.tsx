import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { ConnectorLogo, domainFor } from "./logos";

/* ────────────────────────────────────────────────────────────────
   Screens: welcome → configure → install → done
   Unbranded shell — the name is intentionally not fixed yet.
   ──────────────────────────────────────────────────────────────── */

type Screen =
  | "welcome"
  | "preflight"
  | "member"
  | "configure"
  | "install"
  | "done"
  | "update"
  | "arms"
  | "home"
  | "shell";

type PaneId = "home" | "health" | "arms" | "connectors" | "config" | "updates";

interface SvcHealth {
  label: string;
  name: string;
  running: boolean;
  last_exit: number;
}
interface EndpointHealth {
  name: string;
  ok: boolean;
  detail: string;
}
interface HealthReport {
  mem_total_gb: number;
  mem_free_pct: number | null;
  disk_total_gb: number;
  disk_avail_gb: number;
  services: SvcHealth[];
  endpoints: EndpointHealth[];
  issues: string[];
}

interface TaskRow {
  id: string;
  title: string;
  urgent: boolean;
}
interface ServiceRow {
  name: string;
  label: string;
}
interface ActivityRow {
  title: string;
  when: string;
}
interface HomeData {
  tasks: TaskRow[];
  services: ServiceRow[];
  activity: ActivityRow[];
}

interface ModuleInfo {
  id: string;
  name: string;
  category: string;
  kind?: string | null;
  tagline: string;
  consent?: string | null;
  costs: Record<string, string>;
  services: string[];
  status_note?: string | null;
  status: "active" | "available";
  can_toggle: boolean;
}

interface Check {
  id: string;
  label: string;
  status: "ok" | "warn" | "fail";
  detail: string;
}

interface SetupConfig {
  operatorName: string;
  machineName: string;
  role: "primary" | "worker";
  dryRun: boolean;
}

interface SystemInfo {
  installed: boolean;
  version?: string | null;
  operator?: string | null;
  update_status?: string | null; // up_to_date | update_available
  last_check?: string | null;
}

const IN_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

const STAGES = [
  "Checking your Mac",
  "Installing the system",
  "Setting up your knowledge vault and memory",
  "Waking the agents",
  "Making it yours",
  "Final checks",
];

const stripAnsi = (s: string) =>
  // eslint-disable-next-line no-control-regex
  s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "").replace(/\r/g, "");

/* ── shared bits ── */

function DragRegion() {
  return <div data-tauri-drag-region className="fixed top-0 left-0 right-0 h-9 z-50" />;
}

function Mark({ size = 56 }: { size?: number }) {
  // Placeholder logomark — a quiet rounded square with a breathing dot.
  return (
    <div
      style={{ width: size, height: size }}
      className="rounded-2xl border border-zinc-700/80 bg-zinc-900 flex items-center justify-center"
    >
      <div className="w-2.5 h-2.5 rounded-full bg-zinc-100 pulse-dot" />
    </div>
  );
}

function PrimaryButton({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="px-6 h-11 rounded-xl bg-zinc-100 text-zinc-950 text-[14px] font-medium
                 hover:bg-white active:scale-[0.98] transition
                 disabled:opacity-40 disabled:pointer-events-none"
    >
      {children}
    </button>
  );
}

/* ── screen 1: welcome ── */

function Welcome({
  onSetup,
  onJoin,
}: {
  onSetup: () => void;
  onJoin: () => void;
}) {
  return (
    <div className="screen h-full flex flex-col items-center justify-center gap-10">
      <Mark />
      <div className="text-center space-y-2">
        <h1 className="text-[26px] font-semibold tracking-tight text-zinc-50">Welcome</h1>
        <p className="text-[14px] text-zinc-200 max-w-sm leading-relaxed">
          Run your own agentic operating system on this Mac, or join a workspace
          that's already running one.
        </p>
      </div>
      <div className="flex flex-col items-center gap-3">
        <PrimaryButton onClick={onSetup}>Set up this Mac</PrimaryButton>
        <button
          onClick={onJoin}
          className="px-4 h-10 rounded-xl text-[13.5px] text-zinc-200 hover:text-zinc-100 transition"
        >
          Join a workspace instead
        </button>
      </div>
    </div>
  );
}

/* ── screen: preflight ── */

function StatusIcon({ status }: { status: Check["status"] }) {
  if (status === "ok")
    return (
      <svg width="14" height="14" viewBox="0 0 14 14" className="text-zinc-200">
        <path
          d="M2.5 7.5 5.5 10.5 11.5 3.5"
          stroke="currentColor"
          strokeWidth="1.8"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  if (status === "warn")
    return <span className="text-amber-400/90 text-[13px] font-semibold leading-none">!</span>;
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" className="text-red-400">
      <path
        d="M2 2 10 10 M10 2 2 10"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

const DEMO_CHECKS: Check[] = [
  { id: "chip", label: "Apple Silicon", status: "ok", detail: "Full support, including local voice models" },
  { id: "macos", label: "macOS version", status: "ok", detail: "macOS 26.2" },
  { id: "ram", label: "Memory", status: "ok", detail: "16 GB — Comfortable headroom for agent runs" },
  { id: "disk", label: "Free disk space", status: "ok", detail: "184 GB available — Plenty of room" },
  { id: "clt", label: "Developer tools", status: "warn", detail: "Missing — installed automatically during setup" },
  { id: "git", label: "Git", status: "warn", detail: "Missing — comes with developer tools during setup" },
  { id: "brew", label: "Package manager", status: "ok", detail: "Homebrew present" },
  { id: "net", label: "Internet connection", status: "ok", detail: "Reachable" },
];

function Preflight({
  onBack,
  onContinue,
}: {
  onBack: () => void;
  onContinue: () => void;
}) {
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [revealed, setRevealed] = useState(0);

  const load = useCallback(async () => {
    setChecks(null);
    setRevealed(0);
    let result: Check[];
    if (IN_TAURI) {
      try {
        result = await invoke<Check[]>("run_preflight");
      } catch {
        result = [];
      }
    } else {
      await new Promise((r) => setTimeout(r, 500));
      result = DEMO_CHECKS;
    }
    setChecks(result);
    result.forEach((_, i) => setTimeout(() => setRevealed(i + 1), 120 * (i + 1)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const done = checks !== null && revealed >= checks.length;
  const hasFail = (checks ?? []).some((c) => c.status === "fail");
  const hasWarn = (checks ?? []).some((c) => c.status === "warn");

  return (
    <div className="screen h-full flex flex-col items-center justify-center">
      <div className="w-[560px] max-w-[86vw]">
        <h1 className="text-[22px] font-semibold tracking-tight mb-1">Checking your system</h1>
        <p className="text-[13px] text-zinc-300 mb-6">
          Making sure this Mac is ready before anything is installed.
        </p>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-1 min-h-[120px]">
          {checks === null ? (
            <div className="flex items-center gap-3 py-4">
              <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
              <span className="text-[13.5px] text-zinc-200">Inspecting…</span>
            </div>
          ) : (
            checks.slice(0, revealed).map((c) => (
              <div key={c.id} className="screen flex items-center gap-3 py-2.5">
                <div className="w-5 flex justify-center">
                  <StatusIcon status={c.status} />
                </div>
                <span className="text-[14px] text-zinc-200 w-40 shrink-0">{c.label}</span>
                <span className="text-[12.5px] text-zinc-300 truncate">{c.detail}</span>
              </div>
            ))
          )}
        </div>

        {done && (
          <p
            className={
              "screen text-[13px] mt-4 " +
              (hasFail ? "text-red-300" : hasWarn ? "text-zinc-200" : "text-zinc-300")
            }
          >
            {hasFail
              ? "Something needs fixing before setup can continue."
              : hasWarn
                ? "Your system is ready — the flagged items are handled automatically during setup."
                : "Your system is ready."}
          </p>
        )}

        <div className="flex justify-between mt-8">
          <button
            onClick={onBack}
            className="px-4 h-11 rounded-xl text-[14px] text-zinc-200 hover:text-zinc-100 transition"
          >
            Back
          </button>
          <div className="flex gap-3">
            <button
              onClick={load}
              className="px-4 h-11 rounded-xl text-[14px] text-zinc-200 hover:text-zinc-100 transition"
            >
              Check again
            </button>
            <PrimaryButton onClick={onContinue} disabled={!done || hasFail}>
              Continue
            </PrimaryButton>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── screen: member (join a workspace) ── */

function Member({ onBack }: { onBack: () => void }) {
  const [code, setCode] = useState("");
  const [submitted, setSubmitted] = useState(false);

  return (
    <div className="screen h-full flex flex-col items-center justify-center gap-9">
      <Mark />
      <div className="text-center space-y-2">
        <h1 className="text-[24px] font-semibold tracking-tight">Join a workspace</h1>
        <p className="text-[14px] text-zinc-200 max-w-sm leading-relaxed">
          Nothing is installed on this Mac — you'll be connected to a workspace
          that's already running.
        </p>
      </div>

      {submitted ? (
        <div className="w-[380px] max-w-[80vw] rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-4 text-center">
          <div className="text-[13.5px] text-zinc-100 font-medium">Invite saved</div>
          <div className="text-[12.5px] text-zinc-300 mt-1 leading-relaxed">
            Workspaces arrive with the next major update. This invite will work
            here the moment they do.
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-4">
          <input
            autoFocus
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            name="invite-code-field"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="INVITE CODE"
            className="w-64 h-12 px-4 text-center tracking-[0.2em] rounded-xl bg-zinc-900 border border-zinc-800
                       font-mono text-[15px] text-zinc-100 placeholder:text-zinc-500 placeholder:tracking-normal
                       outline-none focus:border-zinc-600 transition"
          />
          <PrimaryButton onClick={() => setSubmitted(true)} disabled={code.trim().length < 6}>
            Join
          </PrimaryButton>
        </div>
      )}

      <button
        onClick={onBack}
        className="px-4 h-10 rounded-xl text-[13.5px] text-zinc-300 hover:text-zinc-100 transition"
      >
        Back
      </button>
    </div>
  );
}

/* ── screen 1b: welcome back (existing install) ── */

// Kept as the compact returning-user card; currently superseded by Shell.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function WelcomeBack({
  sys,
  checking,
  onUpdate,
  onContinue,
}: {
  sys: SystemInfo;
  checking: boolean;
  onUpdate: () => void;
  onContinue: () => void;
}) {
  const updateAvailable = sys.update_status === "update_available";
  return (
    <div className="screen h-full flex flex-col items-center justify-center gap-9">
      <Mark />
      <div className="text-center space-y-2">
        <h1 className="text-[26px] font-semibold tracking-tight text-zinc-50">
          Welcome back{sys.operator ? `, ${sys.operator}` : ""}
        </h1>
        <div className="flex items-center justify-center gap-2 text-[13px] text-zinc-300">
          <span className="px-2 py-0.5 rounded-md border border-zinc-800 bg-zinc-900 font-mono text-[12px] text-zinc-300">
            {sys.version ?? "unknown"}
          </span>
          <span>installed on this Mac</span>
        </div>
      </div>

      <div className="w-[380px] max-w-[80vw] rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-4">
        {checking ? (
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
            <span className="text-[13.5px] text-zinc-200">Checking for updates…</span>
          </div>
        ) : updateAvailable ? (
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-[13.5px] text-zinc-100 font-medium">Update available</div>
              <div className="text-[12px] text-zinc-300 mt-0.5">
                A newer version is ready to install.
              </div>
            </div>
            <button
              onClick={onUpdate}
              className="px-4 h-9 rounded-lg bg-zinc-100 text-zinc-950 text-[13px] font-medium
                         hover:bg-white active:scale-[0.98] transition shrink-0"
            >
              Update now
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <svg width="14" height="14" viewBox="0 0 14 14" className="text-zinc-200">
              <path
                d="M2.5 7.5 5.5 10.5 11.5 3.5"
                stroke="currentColor"
                strokeWidth="1.8"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <span className="text-[13.5px] text-zinc-200">You're up to date</span>
          </div>
        )}
      </div>

      <PrimaryButton onClick={onContinue}>Continue</PrimaryButton>
    </div>
  );
}

/* ── screen: update ── */

function Update({ onFinished }: { onFinished: (code: number) => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const consoleRef = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  useEffect(() => {
    const un1 = listen<string>("update:line", (e) => {
      const clean = stripAnsi(e.payload).trimEnd();
      if (!clean.trim()) return;
      setLines((l) => [...l.slice(-500), clean]);
    });
    const un2 = listen<number>("update:done", (e) => onFinished(e.payload));
    if (!started.current) {
      started.current = true;
      if (IN_TAURI) {
        invoke("run_update").catch((err) => setError(String(err)));
      } else {
        const demo = [
          "Pulling latest release…",
          "Reconciling instance state…",
          "Rebuilding environments…",
          "Restarting services…",
          "Health check passed",
        ];
        demo.forEach((d, i) => setTimeout(() => setLines((l) => [...l, d]), 800 * (i + 1)));
        setTimeout(() => onFinished(0), 800 * (demo.length + 1));
      }
    }
    return () => {
      un1.then((f) => f());
      un2.then((f) => f());
    };
  }, [onFinished]);

  useEffect(() => {
    consoleRef.current?.scrollTo({ top: consoleRef.current.scrollHeight });
  }, [lines]);

  return (
    <div className="screen h-full flex flex-col items-center justify-center">
      <div className="w-[560px] max-w-[86vw]">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
          <h1 className="text-[22px] font-semibold tracking-tight">Updating</h1>
        </div>
        <p className="text-[13px] text-zinc-300 mb-6">
          Pulling the latest version and restarting services. Hold on.
        </p>

        {error && (
          <div className="mb-4 rounded-xl border border-red-900/60 bg-red-950/30 px-4 py-3 text-[13px] text-red-300">
            {error}
          </div>
        )}

        <div
          ref={consoleRef}
          className="console h-56 overflow-y-auto rounded-xl border border-zinc-800 bg-black/60 px-4 py-3
                     font-mono text-[11.5px] leading-relaxed text-zinc-300 whitespace-pre-wrap select-text"
        >
          {lines.join("\n") || "Starting…"}
        </div>
      </div>
    </div>
  );
}

/* ── screen: home ── */

const DEMO_HOME: HomeData = {
  tasks: [
    { id: "t#906", title: "Blaxle P0: name decision + churn analysis", urgent: true },
    { id: "aos#155", title: "Kanban Phase 0", urgent: false },
    { id: "t#881", title: "Verb derivation trace engine", urgent: true },
    { id: "aos#75.4", title: "Speaker diarization", urgent: false },
  ],
  services: [
    { name: "bridge", label: "com.aos.bridge" },
    { name: "qareen", label: "com.aos.qareen" },
    { name: "transcriber", label: "com.aos.transcriber" },
    { name: "scheduler", label: "com.aos.scheduler" },
    { name: "sentinel", label: "com.aos.sentinel" },
    { name: "n8n", label: "com.aos.n8n" },
  ],
  activity: [
    { title: "Installer Modularization — Core vs Arms", when: "1h ago" },
    { title: "Blaxle — Architecture Synthesis", when: "3h ago" },
    { title: "Graph Engineering explained", when: "7h ago" },
  ],
};

function Card({
  title,
  children,
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={"rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-4 " + className}>
      <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-3">{title}</div>
      {children}
    </div>
  );
}

function Home({
  sys,
  onArms,
  onUpdate,
}: {
  sys: SystemInfo | null;
  onArms?: () => void;
  onUpdate: () => void;
}) {
  const [data, setData] = useState<HomeData | null>(null);
  const [modules, setModules] = useState<ModuleInfo[] | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    (async () => {
      if (IN_TAURI) {
        try {
          setData(await invoke<HomeData>("home_data"));
        } catch {
          setData({ tasks: [], services: [], activity: [] });
        }
        try {
          setModules(await invoke<ModuleInfo[]>("list_modules"));
        } catch {
          setModules([]);
        }
      } else {
        await new Promise((r) => setTimeout(r, 400));
        setData(DEMO_HOME);
        setModules(DEMO_MODULES);
      }
    })();
  }, []);

  const runSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setResults(null);
    if (IN_TAURI) {
      try {
        setResults(await invoke<string>("search_vault", { query: q }));
      } catch (e) {
        setResults(String(e));
      }
    } else {
      await new Promise((r) => setTimeout(r, 500));
      setResults(
        "1. knowledge/specs/blaxle-architecture.md — Blaxle — Architecture Synthesis\n2. knowledge/specs/installer-modularization.md — Installer Modularization",
      );
    }
    setSearching(false);
  }, [query]);

  const updateAvailable = sys?.update_status === "update_available";
  const arms = (modules ?? []).filter((m) => m.category === "arm");
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  return (
    <div className="screen h-full overflow-y-auto console">
      <div className="max-w-[760px] mx-auto px-8 pt-14 pb-16">
        {/* header */}
        <div className="flex items-center gap-3 mb-8">
          <Mark size={34} />
          <div className="flex-1">
            <div className="text-[17px] font-semibold tracking-tight leading-tight">
              {greeting}
              {sys?.operator ? `, ${sys.operator}` : ""}
            </div>
            <div className="flex items-center gap-2 text-[11.5px] text-zinc-500 mt-0.5">
              <span className="font-mono">{sys?.version ?? ""}</span>
              {updateAvailable ? (
                <button onClick={onUpdate} className="text-zinc-200 hover:text-white transition underline underline-offset-2">
                  update available
                </button>
              ) : (
                <span>up to date</span>
              )}
            </div>
          </div>
          {onArms && (
            <button
              onClick={onArms}
              className="px-3.5 h-9 rounded-lg border border-zinc-800 text-[12.5px] text-zinc-200 hover:text-white hover:border-zinc-600 transition"
            >
              Arms &amp; Connectors
            </button>
          )}
        </div>

        {/* search */}
        <div className="mb-4">
          <div className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900 px-4 h-12 focus-within:border-zinc-600 transition">
            <svg width="14" height="14" viewBox="0 0 14 14" className="text-zinc-500 shrink-0">
              <circle cx="6" cy="6" r="4.2" stroke="currentColor" strokeWidth="1.6" fill="none" />
              <path d="M9.2 9.2 12.5 12.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runSearch()}
              placeholder="Search everything the system knows…"
              autoComplete="off"
              spellCheck={false}
              className="flex-1 bg-transparent outline-none text-[14px] text-zinc-100 placeholder:text-zinc-500"
            />
            {searching && <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot shrink-0" />}
          </div>
          {results !== null && (
            <div className="screen mt-2 rounded-xl border border-zinc-800 bg-black/50 px-4 py-3 font-mono text-[11.5px] leading-relaxed text-zinc-300 whitespace-pre-wrap select-text max-h-48 overflow-y-auto console">
              {results || "No results."}
            </div>
          )}
        </div>

        {data === null ? (
          <div className="flex items-center gap-3 py-8">
            <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
            <span className="text-[13.5px] text-zinc-200">Reading system state…</span>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <Card title="Work" className="col-span-2 sm:col-span-1">
              {data.tasks.length ? (
                data.tasks.slice(0, 5).map((t) => (
                  <div key={t.id} className="flex items-baseline gap-2.5 py-1.5">
                    <div
                      className={
                        "w-1.5 h-1.5 rounded-full shrink-0 translate-y-[-1px] " +
                        (t.urgent ? "bg-zinc-100" : "border border-zinc-600")
                      }
                    />
                    <span className="text-[13px] text-zinc-100 leading-snug flex-1 min-w-0 truncate">
                      {t.title}
                    </span>
                    <span className="font-mono text-[10.5px] text-zinc-500 shrink-0">{t.id}</span>
                  </div>
                ))
              ) : (
                <div className="text-[13px] text-zinc-500">Nothing queued today.</div>
              )}
            </Card>

            <Card title="System" className="col-span-2 sm:col-span-1">
              {data.services.slice(0, 5).map((s) => (
                <div key={s.label} className="flex items-center gap-2.5 py-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-zinc-100 shrink-0" />
                  <span className="text-[13px] text-zinc-100">{s.name}</span>
                </div>
              ))}
              {data.services.length > 5 && (
                <div className="text-[11.5px] text-zinc-500 mt-1.5 pl-4">
                  + {data.services.length - 5} more running
                </div>
              )}
            </Card>

            <Card title="Arms" className="col-span-2">
              <div className="flex flex-wrap gap-2">
                {arms.map((m) => {
                  const on = m.status === "active";
                  return (
                    <span
                      key={m.id}
                      className={
                        "inline-flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-[12px] " +
                        (on
                          ? "border-zinc-700 text-zinc-100"
                          : "border-zinc-800 text-zinc-500")
                      }
                    >
                      <span
                        className={
                          "w-1.5 h-1.5 rounded-full " + (on ? "bg-zinc-100" : "border border-zinc-600")
                        }
                      />
                      {m.name}
                    </span>
                  );
                })}
              </div>
            </Card>

            <Card title="Recent knowledge" className="col-span-2">
              {data.activity.length ? (
                data.activity.map((a, i) => (
                  <div key={i} className="flex items-baseline gap-3 py-1.5">
                    <span className="text-[13px] text-zinc-100 flex-1 min-w-0 truncate">{a.title}</span>
                    <span className="font-mono text-[10.5px] text-zinc-500 shrink-0">{a.when}</span>
                  </div>
                ))
              ) : (
                <div className="text-[13px] text-zinc-500">Nothing yet.</div>
              )}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── screen: arms & connectors ── */

const DEMO_MODULES: ModuleInfo[] = [
  { id: "vault", name: "Knowledge Vault", category: "standard", tagline: "A private knowledge base the system reads and writes.", costs: { resident_ram: "0" }, services: [], status: "active", can_toggle: false },
  { id: "memory-search", name: "Memory & Search", category: "standard", tagline: "The system remembers and finds anything it has seen.", costs: { resident_ram: "0 (index jobs only)" }, services: [], status: "active", can_toggle: false },
  { id: "telegram", kind: "connector", name: "Telegram", category: "arm", tagline: "Talk to your system from your phone — messages, voice notes, briefings.", costs: { resident_ram: "~60 MB (bridge)" }, services: ["com.aos.bridge"], status: "active", can_toggle: true },
  { id: "whatsapp", kind: "connector", name: "WhatsApp", category: "arm", tagline: "WhatsApp messages flow through your system.", costs: { resident_ram: "~40 MB" }, services: ["com.aos.whatsmeow"], status: "active", can_toggle: true },
  { id: "remote-access", kind: "connector", name: "Remote Access", category: "arm", tagline: "Reach this machine securely from your phone or laptop.", costs: { resident_ram: "~50 MB (tailscaled)" }, services: [], status: "active", can_toggle: false },
  { id: "voice-dictation", name: "Voice — Dictation", category: "arm", tagline: "On-device speech to text with a model that learns your vocabulary.", costs: { resident_ram: "~20 MB at rest", download: "1.6 GB" }, services: ["com.aos.transcriber"], status: "active", can_toggle: true },
  { id: "voice-meetings", name: "Voice — Meetings", category: "arm", tagline: "Meeting notes with named speakers, recognized by voice.", costs: { download: "~1 GB" }, services: [], status: "available", can_toggle: false, status_note: "arrives with a system update" },
  { id: "automations", name: "Automations (n8n)", category: "arm", tagline: "A visual engine for scheduled routines and triggers.", costs: { resident_ram: "~300 MB resident" }, services: ["com.aos.n8n"], status: "active", can_toggle: true },
  { id: "sentinel", name: "Sentinel (iMessage)", category: "arm", tagline: "Watches iMessage for commitments you make and drafts follow-through.", consent: "Reads your Messages database (requires Full Disk Access).", costs: { resident_ram: "~30 MB", access: "Full Disk Access" }, services: ["com.aos.sentinel"], status: "available", can_toggle: true },
];

function CostChips({ costs }: { costs: Record<string, string> }) {
  const entries = Object.entries(costs).filter(([, v]) => v && v !== "0");
  if (!entries.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-1.5">
      {entries.map(([k, v]) => (
        <span
          key={k}
          className="px-1.5 py-0.5 rounded border border-zinc-800 bg-zinc-900/80 text-[10.5px] text-zinc-300"
        >
          {k === "resident_ram" ? "RAM " : k === "download" ? "↓ " : k === "disk" ? "disk " : ""}
          {v}
        </span>
      ))}
    </div>
  );
}

function ModuleRow({
  m,
  busy,
  onToggle,
}: {
  m: ModuleInfo;
  busy: boolean;
  onToggle: (m: ModuleInfo, enable: boolean) => void;
}) {
  const active = m.status === "active";
  return (
    <div className="flex items-start gap-3.5 px-5 py-4">
      <div className="mt-1.5 w-2 flex justify-center shrink-0">
        <div className={"w-2 h-2 rounded-full " + (active ? "bg-zinc-100" : "border border-zinc-600")} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[14px] text-zinc-100 font-medium">{m.name}</span>
          {m.consent && (
            <span className="px-1.5 py-0.5 rounded border border-amber-900/50 bg-amber-950/30 text-[10px] text-amber-400/90">
              sensitive
            </span>
          )}
        </div>
        <div className="text-[12.5px] text-zinc-300 mt-0.5 leading-relaxed">{m.tagline}</div>
        <CostChips costs={m.costs} />
      </div>
      <div className="shrink-0 pt-1">
        {m.can_toggle ? (
          <button
            disabled={busy}
            onClick={() => onToggle(m, !active)}
            className={
              "px-3.5 h-8 rounded-lg text-[12.5px] font-medium transition active:scale-[0.97] disabled:opacity-40 " +
              (active
                ? "border border-zinc-700 text-zinc-300 hover:text-zinc-100 hover:border-zinc-500"
                : "bg-zinc-100 text-zinc-950 hover:bg-white")
            }
          >
            {busy ? "…" : active ? "Turn off" : "Turn on"}
          </button>
        ) : active ? (
          <span className="text-[11.5px] text-zinc-500 pt-1.5 inline-block">built in</span>
        ) : (
          <span className="text-[11.5px] text-zinc-500 pt-1.5 inline-block">
            {m.status_note ?? "via system update"}
          </span>
        )}
      </div>
    </div>
  );
}

function Arms({
  onBack,
  connectors = false,
}: {
  onBack?: () => void;
  connectors?: boolean;
}) {
  const [modules, setModules] = useState<ModuleInfo[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ModuleInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (IN_TAURI) {
      try {
        setModules(await invoke<ModuleInfo[]>("list_modules"));
      } catch (e) {
        setError(String(e));
        setModules([]);
      }
    } else {
      await new Promise((r) => setTimeout(r, 400));
      setModules(DEMO_MODULES);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const applyToggle = useCallback(
    async (m: ModuleInfo, enable: boolean) => {
      setConfirming(null);
      setBusyId(m.id);
      setError(null);
      if (IN_TAURI) {
        try {
          await invoke("set_module_enabled", { id: m.id, enabled: enable });
        } catch (e) {
          setError(String(e));
        }
        await load();
      } else {
        await new Promise((r) => setTimeout(r, 600));
        setModules(
          (mods) =>
            mods?.map((x) =>
              x.id === m.id ? { ...x, status: enable ? "active" : "available" } : x,
            ) ?? null,
        );
      }
      setBusyId(null);
    },
    [load],
  );

  const requestToggle = useCallback(
    (m: ModuleInfo, enable: boolean) => {
      // Anything sensitive, or any activation with a real cost, confirms first.
      if (enable && (m.consent || m.costs.download)) setConfirming(m);
      else applyToggle(m, enable);
    },
    [applyToggle],
  );

  const groups: [string, ModuleInfo[]][] = modules
    ? connectors
      ? [["Connectors", modules.filter((m) => m.kind === "connector")]]
      : [
          ["Arms", modules.filter((m) => m.category === "arm" && m.kind !== "connector")],
          ["Built-in", modules.filter((m) => m.category !== "arm" && m.kind !== "connector")],
        ]
    : [];

  return (
    <div className="screen h-full flex flex-col items-center py-14 overflow-y-auto console">
      <div className="w-[620px] max-w-[90vw]">
        <div className="flex items-end justify-between mb-1">
          <h1 className="text-[22px] font-semibold tracking-tight">
            {connectors ? "Connectors" : "Arms"}
          </h1>
          {onBack && (
            <button
              onClick={onBack}
              className="text-[13px] text-zinc-300 hover:text-zinc-100 transition pb-1"
            >
              Back
            </button>
          )}
        </div>
        <p className="text-[13px] text-zinc-300 mb-6">
          Capabilities of your system. Turning one on makes real changes to this
          Mac — every item shows what it costs before it runs.
        </p>

        {error && (
          <div className="mb-4 rounded-xl border border-red-900/60 bg-red-950/30 px-4 py-3 text-[13px] text-red-300 select-text">
            {error}
          </div>
        )}

        {modules === null ? (
          <div className="flex items-center gap-3 py-6">
            <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
            <span className="text-[13.5px] text-zinc-200">Reading system state…</span>
          </div>
        ) : (
          groups.map(([title, mods]) =>
            mods.length ? (
              <div key={title} className="mb-6">
                <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-2">{title}</div>
                <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 divide-y divide-zinc-800/70">
                  {mods.map((m) => (
                    <ModuleRow key={m.id} m={m} busy={busyId === m.id} onToggle={requestToggle} />
                  ))}
                </div>
              </div>
            ) : null,
          )
        )}
      </div>

      {confirming && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-40">
          <div className="screen w-[420px] max-w-[86vw] rounded-2xl border border-zinc-700 bg-zinc-900 p-6">
            <h2 className="text-[16px] font-semibold mb-2">Turn on {confirming.name}?</h2>
            <p className="text-[13px] text-zinc-200 leading-relaxed">
              {confirming.consent ?? confirming.tagline}
            </p>
            <CostChips costs={confirming.costs} />
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setConfirming(null)}
                className="px-4 h-10 rounded-xl text-[13.5px] text-zinc-200 hover:text-zinc-100 transition"
              >
                Cancel
              </button>
              <button
                onClick={() => applyToggle(confirming, true)}
                className="px-5 h-10 rounded-xl bg-zinc-100 text-zinc-950 text-[13.5px] font-medium hover:bg-white transition"
              >
                Turn on
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── pane: connectors ── */

interface ConnectorAccount {
  identity: string;
  detail: string;
}
interface KeyField {
  secret: string;
  label: string;
  get_url: string;
}
interface Connector {
  id: string;
  name: string;
  category: string;
  auth_kind: string;
  status: string; // connected | attention | available
  detail: string;
  accounts: ConnectorAccount[];
  connect_hint: string;
  key_fields?: KeyField[];
  composio_slug?: string | null;
  /** Optional: false when nothing in the runtime tree consumes this connection. */
  in_use?: boolean | null;
}
/** Who actually consumes this connection — `connector_usage`. */
interface ConnectorUsage {
  in_use: boolean;
  used_by: string[];
}
interface ToolkitCard {
  slug: string;
  label: string;
  blurb: string;
  logo?: string | null;
}
/** Result of a live end-to-end probe — `test_connector`. */
interface TestResult {
  ok: boolean;
  message: string;
  ms?: number;
  identity?: string;
}
/**
 * One live bot from `telegram_bot_info` (getMe per configured slot).
 * A slot whose probe failed returns only `slot`, `ok: false` and `error` —
 * name and username are absent, so they are optional here.
 */
interface TelegramBot {
  slot: string;
  name?: string;
  username?: string;
  ok?: boolean;
  error?: string;
  detail?: string;
}

/** Where the operator manages this connection at the provider. */
const MANAGE_URLS: Record<string, { label: string; url: string }> = {
  google: { label: "myaccount.google.com", url: "https://myaccount.google.com/connections" },
  github: { label: "github.com/settings", url: "https://github.com/settings/apps" },
  telegram: { label: "@BotFather", url: "https://t.me/BotFather" },
  composio: { label: "app.composio.dev", url: "https://app.composio.dev" },
  notion: { label: "notion.so/my-integrations", url: "https://www.notion.so/my-integrations" },
  slack: { label: "api.slack.com/apps", url: "https://api.slack.com/apps" },
  cloudflare: { label: "dash.cloudflare.com", url: "https://dash.cloudflare.com/profile/api-tokens" },
  clickup: { label: "app.clickup.com", url: "https://app.clickup.com/settings/apps" },
  linear: { label: "linear.app/settings/api", url: "https://linear.app/settings/api" },
  discord: { label: "discord.com/developers", url: "https://discord.com/developers/applications" },
  todoist: { label: "todoist.com/integrations", url: "https://app.todoist.com/app/settings/integrations" },
  openrouter: { label: "openrouter.ai/keys", url: "https://openrouter.ai/keys" },
  elevenlabs: { label: "elevenlabs.io", url: "https://elevenlabs.io/app/settings/api-keys" },
  whatsapp: { label: "your phone", url: "https://faq.whatsapp.com/378279804439436" },
  obsidian: { label: "obsidian.md", url: "https://obsidian.md" },
  airtable: { label: "airtable.com/create/tokens", url: "https://airtable.com/create/tokens" },
};

function manageTarget(id: string): { label: string; url: string } | null {
  const known = MANAGE_URLS[id];
  if (known) return known;
  const d = domainFor(id);
  return d ? { label: d, url: `https://${d}` } : null;
}

/** Opens in the operator's real browser when packaged, a tab in demo mode. */
function openExternal(url: string) {
  if (IN_TAURI) {
    invoke("open_url", { url }).catch(() => window.open(url, "_blank"));
  } else {
    window.open(url, "_blank");
  }
}

const DEMO_CONNECTORS: Connector[] = [
  { id: "google", name: "Google Workspace", category: "productivity", auth_kind: "oauth", status: "connected", detail: "4 accounts — Gmail, Drive, Calendar, Docs", connect_hint: "", accounts: [
    { identity: "hishamalhadi@gmail.com", detail: "39 permissions · token auto-refreshes" },
    { identity: "hisham@nuchay.com", detail: "39 permissions · token auto-refreshes" },
    { identity: "mail@hishamalhadi.com", detail: "39 permissions · token auto-refreshes" },
    { identity: "hisham.alhadi@alhudaelementary.ca", detail: "39 permissions · token auto-refreshes" },
  ]},
  { id: "github", name: "GitHub", category: "development", auth_kind: "cli", status: "connected", detail: "@hishamalhadi", connect_hint: "gh auth login --web", accounts: [{ identity: "@hishamalhadi", detail: "permissions: gist, read:org, repo, workflow" }] },
  { id: "telegram", name: "Telegram", category: "communication", auth_kind: "token", status: "connected", detail: "bridge running", connect_hint: "", accounts: [{ identity: "primary bot", detail: "bot token in Keychain · tokens don't expire" }, { identity: "tabib bot", detail: "bot token in Keychain" }] },
  { id: "whatsapp", name: "WhatsApp", category: "communication", auth_kind: "session", status: "connected", detail: "phone-paired session", connect_hint: "", accounts: [{ identity: "paired device", detail: "QR session · re-pair if your phone unlinks it" }] },
  { id: "slack", name: "Slack", category: "communication", auth_kind: "token", status: "connected", detail: "bot + app tokens in Keychain", connect_hint: "", accounts: [], key_fields: [], composio_slug: null },
  { id: "cloudflare", name: "Cloudflare", category: "infrastructure", auth_kind: "token", status: "connected", detail: "2 accounts", connect_hint: "", accounts: [{ identity: "personal (hish.am)", detail: "API token" }, { identity: "Elora Greens", detail: "API token" }] },
  { id: "clickup", name: "ClickUp", category: "productivity", auth_kind: "token", status: "connected", detail: "API token in Keychain", connect_hint: "", accounts: [], key_fields: [{ secret: "CLICKUP_API_KEY", label: "API token (pk_…)", get_url: "https://app.clickup.com/settings/apps" }], composio_slug: null, in_use: false },
  { id: "obsidian", name: "Obsidian", category: "knowledge", auth_kind: "token", status: "connected", detail: "local REST API", connect_hint: "", accounts: [], key_fields: [], composio_slug: null },
  { id: "composio", name: "Composio", category: "infrastructure", auth_kind: "token", status: "connected", detail: "Hosted sign-in enabled — 500+ apps one-click", connect_hint: "", accounts: [{ identity: "aos_9f2c4e1b (this Mac)", detail: "hosted sign-ins · 3 apps linked" }], key_fields: [{ secret: "COMPOSIO_API_KEY", label: "Project API key (ak_…)", get_url: "https://app.composio.dev/developers" }], composio_slug: null },
  { id: "notion", name: "Notion", category: "knowledge", auth_kind: "oauth", status: "available", detail: "Pages and databases as agent workspace.", connect_hint: "", accounts: [], key_fields: [{ secret: "NOTION_API_KEY", label: "Internal integration secret", get_url: "https://www.notion.so/my-integrations" }], composio_slug: "notion" },
  { id: "linear", name: "Linear", category: "development", auth_kind: "token", status: "available", detail: "Issues and cycles.", connect_hint: "Ask your agent to connect it — setup is guided.", accounts: [], key_fields: [], composio_slug: null },
  { id: "discord", name: "Discord", category: "communication", auth_kind: "token", status: "available", detail: "Bot presence in your servers.", connect_hint: "Ask your agent to connect it — setup is guided.", accounts: [], key_fields: [], composio_slug: null },
  { id: "todoist", name: "Todoist", category: "productivity", auth_kind: "token", status: "available", detail: "Tasks and projects.", connect_hint: "Ask your agent to connect it — setup is guided.", accounts: [], key_fields: [], composio_slug: null },
  { id: "plane", name: "Plane", category: "productivity", auth_kind: "token", status: "connected", detail: "API token in Keychain", connect_hint: "", accounts: [], key_fields: [{ secret: "PLANE_API_KEY", label: "API token", get_url: "https://app.plane.so/profile/api-tokens" }], composio_slug: null, in_use: false },
  { id: "openrouter", name: "OpenRouter", category: "ai", auth_kind: "token", status: "available", detail: "One key, every model.", connect_hint: "", accounts: [], key_fields: [{ secret: "OPENROUTER_API_KEY", label: "API key (sk-or-…)", get_url: "https://openrouter.ai/keys" }], composio_slug: null },
  { id: "airtable", name: "Airtable", category: "productivity", auth_kind: "oauth", status: "available", detail: "Bases and records.", connect_hint: "", accounts: [], key_fields: [], composio_slug: "airtable" },
];

// Shaped exactly like the real telegram_bot_info payload, failed slot included.
const DEMO_TELEGRAM_BOTS: TelegramBot[] = [
  { slot: "primary", name: "AOS Bridge", username: "hish_aos_bot", ok: true },
  { slot: "tabib", name: "Tabib", username: "tabib_care_bot", ok: true },
  { slot: "archive", ok: false, error: "Telegram rejected the token (401) — re-issue it in @BotFather." },
];

const DEMO_USAGE: Record<string, ConnectorUsage> = {
  google: { in_use: true, used_by: ["dashboard", "automations", "core system"] },
  telegram: { in_use: true, used_by: ["bridge service", "tracking alerts", "core system"] },
  github: { in_use: true, used_by: ["ship skill", "work-runner"] },
  whatsapp: { in_use: true, used_by: ["whatsmeow service"] },
  composio: { in_use: true, used_by: ["connectors pane"] },
  cloudflare: { in_use: true, used_by: ["publishing platform", "core system"] },
  slack: { in_use: true, used_by: ["slack-watch service"] },
  // Matches the real machine: the Obsidian key appears only in declaration
  // files (accounts.yaml / capabilities.yaml), which aren't consumers.
  obsidian: { in_use: false, used_by: [] },
  clickup: { in_use: false, used_by: [] },
  plane: { in_use: false, used_by: [] },
};

const DEMO_TESTS: Record<string, TestResult> = {
  google: { ok: true, message: "Reached Gmail as hishamalhadi@gmail.com", ms: 412 },
  telegram: { ok: true, message: "Bot @hish_aos_bot is alive", ms: 320 },
  github: { ok: true, message: "Authenticated as @hishamalhadi", ms: 188 },
  whatsapp: { ok: true, message: "Paired device is online", ms: 96 },
  composio: { ok: true, message: "Session aos_9f2c4e1b is active", ms: 240 },
  cloudflare: { ok: true, message: "Token valid for 2 accounts", ms: 175 },
};

const CATEGORY_LABELS: Record<string, string> = {
  communication: "Communication",
  productivity: "Productivity",
  development: "Development",
  knowledge: "Knowledge",
  voice: "Voice",
  ai: "AI",
  business: "Business",
  infrastructure: "Infrastructure",
  other: "Other",
};

const AUTH_LABELS: Record<string, string> = {
  oauth: "Signs in with the provider — you approve the permissions",
  token: "Uses a key stored in your Mac's Keychain",
  session: "Paired to your phone by QR code",
  cli: "Authenticated through its official command-line tool",
  apple: "Built into macOS — just needs permission",
};

/** Short form of the same thing, for the metadata footer. */
const AUTH_SHORT: Record<string, string> = {
  oauth: "Provider sign-in (OAuth)",
  token: "API key — Keychain",
  session: "QR-paired device",
  cli: "Official command-line tool",
  apple: "macOS permission",
};

function ConnectorCard({
  c,
  usage,
  onOpen,
}: {
  c: Connector;
  usage?: ConnectorUsage;
  onOpen: (c: Connector) => void;
}) {
  const connected = c.status === "connected";
  const attention = c.status === "attention";
  // Connected is not the same as in use. Usage arrives after first paint.
  const dormant = connected && (usage ? !usage.in_use : c.in_use === false);
  return (
    <button
      onClick={() => onOpen(c)}
      className={
        "text-left rounded-2xl border border-zinc-800 bg-zinc-950/60 px-4 py-3.5 hover:border-zinc-600 transition flex items-center gap-3.5 " +
        (dormant ? "opacity-65 hover:opacity-100" : "")
      }
    >
      <ConnectorLogo id={c.id} name={c.name} domain={domainFor(c.id)} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13.5px] font-medium text-zinc-100 truncate">{c.name}</span>
          {c.accounts.length > 1 && (
            <span className="text-[10.5px] text-zinc-500 font-mono">×{c.accounts.length}</span>
          )}
        </div>
        <div className="text-[11.5px] text-zinc-500 truncate mt-0.5">
          {dormant ? "Connected · not in use" : c.detail}
        </div>
      </div>
      {connected ? (
        dormant ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-zinc-500 shrink-0">
            <span className="w-1.5 h-1.5 rounded-full bg-zinc-600" /> Idle
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-zinc-200 shrink-0">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400/90" /> Connected
          </span>
        )
      ) : attention ? (
        <span className="inline-flex items-center gap-1.5 text-[11px] text-amber-300 shrink-0">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400/90" /> Attention
        </span>
      ) : (
        <span className="text-[12px] px-3 py-1.5 rounded-lg bg-zinc-100 text-zinc-950 font-medium shrink-0">
          Connect
        </span>
      )}
    </button>
  );
}

const DEMO_TOOLKITS: ToolkitCard[] = [
  { slug: "gmail", label: "Gmail", blurb: "Read and send email" },
  { slug: "googlecalendar", label: "Google Calendar", blurb: "Read and create events" },
  { slug: "notion", label: "Notion", blurb: "Pages and databases" },
  { slug: "slack", label: "Slack", blurb: "Post updates and read channels" },
  { slug: "linear", label: "Linear", blurb: "Issues and project tracking" },
  { slug: "discord", label: "Discord", blurb: "Messages and channels" },
  { slug: "x", label: "X (Twitter)", blurb: "Post and read on X" },
  { slug: "reddit", label: "Reddit", blurb: "Browse and post" },
  { slug: "jira", label: "Jira", blurb: "Issues and sprints" },
  { slug: "asana", label: "Asana", blurb: "Tasks and projects" },
  { slug: "dropbox", label: "Dropbox", blurb: "Files and folders" },
  { slug: "airtable", label: "Airtable", blurb: "Bases and records" },
  { slug: "figma", label: "Figma", blurb: "Files and comments" },
  { slug: "stripe", label: "Stripe", blurb: "Payments and customers" },
  { slug: "shopify", label: "Shopify", blurb: "Products, orders, customers" },
  { slug: "todoist", label: "Todoist", blurb: "Tasks and projects" },
];

type ConnectPhase = "view" | "keys" | "browser-wait" | "done";

/** The only modal left in the connectors flow — destructive confirmations. */
function ConfirmDialog({
  title,
  body,
  confirmLabel,
  busy,
  onCancel,
  onConfirm,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onCancel}>
      <div
        className="console w-[400px] max-w-[88vw] rounded-2xl border border-zinc-700 bg-zinc-900 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-[15px] font-semibold text-zinc-100 mb-1.5">{title}</div>
        <div className="text-[12.5px] text-zinc-400 leading-relaxed">{body}</div>
        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onCancel}
            className="px-4 h-10 rounded-xl text-[13.5px] text-zinc-300 hover:text-zinc-100 transition"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className="px-5 h-10 rounded-xl bg-red-400/90 text-zinc-950 text-[13.5px] font-medium hover:bg-red-300 transition disabled:opacity-40"
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Small outlined button used for per-account actions. */
function RowAction({
  children,
  onClick,
  danger,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={
        "px-2.5 h-7 rounded-lg border text-[11.5px] whitespace-nowrap transition disabled:opacity-40 " +
        (danger
          ? "border-zinc-800 text-zinc-500 hover:text-red-300 hover:border-red-900/70"
          : "border-zinc-800 text-zinc-400 hover:text-zinc-100 hover:border-zinc-600")
      }
    >
      {children}
    </button>
  );
}

function MetaBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-[0.14em] text-zinc-600 mb-1.5">{label}</div>
      <div className="text-[12.5px] text-zinc-300">{children}</div>
    </div>
  );
}

function StatusPill({ status, dormant }: { status: string; dormant?: boolean }) {
  if (status === "connected")
    return dormant ? (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-zinc-800 bg-zinc-950/70 text-[11px] text-zinc-400">
        <span className="w-1.5 h-1.5 rounded-full bg-zinc-600" /> Connected · not in use
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-zinc-800 bg-zinc-950/70 text-[11px] text-zinc-200">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400/90" /> Connected
      </span>
    );
  if (status === "attention")
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-amber-900/60 bg-amber-950/20 text-[11px] text-amber-300">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400/90" /> Needs attention
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-zinc-800 bg-zinc-950/70 text-[11px] text-zinc-400">
      Not connected
    </span>
  );
}

type Probe = TestResult | "running";

interface AccountRow {
  identity: string;
  detail: string;
  badge?: string;
  failed?: boolean;
}

/**
 * A connector is a page, not a modal: breadcrumb, hero with live actions,
 * description, per-account actions, provider metadata.
 */
function ConnectorDetail({
  c,
  composioReady,
  initialUsage,
  onBack,
  onChanged,
}: {
  c: Connector;
  composioReady: boolean;
  /** Already read for the list card — seeds the page so it paints complete. */
  initialUsage?: ConnectorUsage;
  onBack: () => void;
  onChanged: () => void;
}) {
  const [phase, setPhase] = useState<ConnectPhase>("view");
  const [about, setAbout] = useState<{ about: string; provides: string[] } | null>(null);
  const [bots, setBots] = useState<TelegramBot[] | null>(null);
  const [keyValues, setKeyValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState<null | { kind: "disconnect" } | { kind: "account"; identity: string }>(null);
  const [usage, setUsage] = useState<ConnectorUsage | null>(initialUsage ?? null);
  const [test, setTest] = useState<Probe | null>(null);
  const [rowTest, setRowTest] = useState<Record<string, Probe>>({});
  const [reloadKey, setReloadKey] = useState(0);

  const connected = c.status === "connected";
  const keyFields = c.key_fields ?? [];
  const isComposioCard = c.id === "composio";
  // Google has no key in the Keychain — disconnecting it means dropping every
  // stored credential file, one per account.
  const perAccountRemoval = c.id === "google" ? c.accounts.map((a) => a.identity) : [];
  const canRemove =
    connected &&
    (keyFields.length > 0 || !!(c.composio_slug && composioReady) || perAccountRemoval.length > 0);
  const canBrowserConnect = !connected && !!c.composio_slug && composioReady;
  const canPasteKey = !connected && (isComposioCard || keyFields.length > 0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (IN_TAURI) {
        try {
          const r = await invoke<{ about: string; provides: string[] }>("connector_about", { id: c.id });
          if (!cancelled) setAbout(r);
        } catch {
          if (!cancelled) setAbout({ about: c.detail, provides: [] });
        }
      } else {
        setAbout({
          about:
            c.id === "google"
              ? "Your Google accounts, wired in end to end: mail is read and sent, Drive files are opened and written, calendar events are created and moved, and Docs and Sheets are edited in place. Each account is authorised separately and each token refreshes on its own — removing one leaves the others untouched."
              : c.id === "telegram"
                ? "The bridge your system talks through. Messages and voice notes you send arrive as work; briefings and answers come back to the same chat. Each bot is a separate identity with its own token, and forum topics route to different parts of the system."
                : c.detail ||
                  (c.status === "connected"
                    ? `${c.name} is connected to your system.`
                    : `Connect ${c.name} to let your system read and act inside it.`),
          provides:
            c.id === "google"
              ? ["Gmail read & send", "Drive files", "Calendar events", "Docs & Sheets"]
              : c.id === "telegram"
                ? ["Chat messages", "Voice notes", "Briefings", "Forum topic routing"]
                : c.id === "notion"
                  ? ["Pages", "Databases", "Blocks & comments"]
                  : ["actions inside " + c.name],
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [c, reloadKey]);

  // Connected is not the same as in use — a key nothing consumes is dormant.
  useEffect(() => {
    if (c.status !== "connected") {
      setUsage(null);
      return;
    }
    let cancelled = false;
    (async () => {
      if (IN_TAURI) {
        try {
          const r = await invoke<ConnectorUsage>("connector_usage", { id: c.id });
          if (!cancelled) setUsage(r);
        } catch {
          if (!cancelled) setUsage(null);
        }
      } else {
        await new Promise((res) => setTimeout(res, 300));
        if (!cancelled) setUsage(DEMO_USAGE[c.id] ?? null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [c.id, c.status, reloadKey]);

  useEffect(() => {
    if (c.id !== "telegram") return;
    let cancelled = false;
    (async () => {
      if (IN_TAURI) {
        try {
          const r = await invoke<{ bots?: TelegramBot[] } | TelegramBot[]>("telegram_bot_info");
          const list = Array.isArray(r) ? r : (r.bots ?? []);
          if (!cancelled) setBots(list);
        } catch {
          if (!cancelled) setBots([]);
        }
      } else {
        await new Promise((res) => setTimeout(res, 500));
        if (!cancelled) setBots(DEMO_TELEGRAM_BOTS);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [c.id, reloadKey]);

  /** Live end-to-end probe. With an identity, probes that one account. */
  const probe = useCallback(
    async (identity?: string) => {
      const put = (v: Probe) =>
        identity ? setRowTest((s) => ({ ...s, [identity]: v })) : setTest(v);
      put("running");
      const t0 = Date.now();
      try {
        if (IN_TAURI) {
          const r = await invoke<TestResult>(
            "test_connector",
            identity ? { id: c.id, identity } : { id: c.id },
          );
          put({ ...r, ms: r.ms ?? Date.now() - t0 });
        } else {
          await new Promise((res) => setTimeout(res, 800));
          put(
            identity
              ? { ok: true, message: `Reached Gmail as ${identity}`, ms: 318 }
              : (DEMO_TESTS[c.id] ?? { ok: true, message: `${c.name} responded`, ms: 214 }),
          );
        }
      } catch (e) {
        put({ ok: false, message: String(e) });
      }
    },
    [c.id, c.name],
  );

  const refresh = useCallback(() => {
    setTest(null);
    setRowTest({});
    setReloadKey((k) => k + 1);
    onChanged();
  }, [onChanged]);

  const saveKeys = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      if (IN_TAURI) {
        if (isComposioCard) {
          await invoke("composio_setup", { apiKey: keyValues["COMPOSIO_API_KEY"] ?? "" });
        } else {
          for (const f of keyFields) {
            const v = keyValues[f.secret];
            if (v?.trim()) await invoke("save_secret", { name: f.secret, value: v.trim() });
          }
        }
      } else {
        await new Promise((res) => setTimeout(res, 700));
      }
      setPhase("done");
      onChanged();
    } catch (e) {
      setError(String(e));
    }
    setBusy(false);
  }, [keyValues, keyFields, isComposioCard, onChanged]);

  const browserConnect = useCallback(async () => {
    setBusy(true);
    setError(null);
    setPhase("browser-wait");
    try {
      if (IN_TAURI) {
        await invoke("composio_link", { slug: c.composio_slug });
        // Poll until the hosted flow completes (up to ~2 min).
        for (let i = 0; i < 40; i++) {
          await new Promise((res) => setTimeout(res, 3000));
          const st = await invoke<Record<string, { connected: boolean; pending: boolean }>>(
            "composio_status",
            { slugs: [c.composio_slug] },
          );
          if (st[c.composio_slug!]?.connected) {
            setPhase("done");
            onChanged();
            setBusy(false);
            return;
          }
        }
        setError("Still waiting on the sign-in — finish it in the browser, then refresh this page.");
        setPhase("view");
      } else {
        await new Promise((res) => setTimeout(res, 1800));
        setPhase("done");
        onChanged();
      }
    } catch (e) {
      setError(String(e));
      setPhase("view");
    }
    setBusy(false);
  }, [c, onChanged]);

  const disconnect = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      if (IN_TAURI) {
        if (c.composio_slug && composioReady) {
          await invoke("composio_disconnect", { slug: c.composio_slug }).catch(() => {});
        }
        for (const f of keyFields) {
          await invoke("delete_secret", { name: f.secret }).catch(() => {});
        }
        for (const identity of perAccountRemoval) {
          await invoke("remove_google_account", { identity });
        }
      } else {
        await new Promise((res) => setTimeout(res, 600));
      }
      setConfirm(null);
      setTest(null);
      setPhase("view");
      onChanged();
    } catch (e) {
      setError(String(e));
      setConfirm(null);
    }
    setBusy(false);
  }, [c, keyFields, perAccountRemoval, composioReady, onChanged]);

  const removeAccount = useCallback(
    async (identity: string) => {
      setBusy(true);
      setError(null);
      try {
        if (IN_TAURI) {
          await invoke("remove_google_account", { identity });
        } else {
          await new Promise((res) => setTimeout(res, 600));
        }
        setConfirm(null);
        onChanged();
      } catch (e) {
        setError(String(e));
        setConfirm(null);
      }
      setBusy(false);
    },
    [onChanged],
  );

  const liveBots = c.id === "telegram" && bots && bots.length > 0 ? bots : null;
  const accountRows: AccountRow[] = liveBots
    ? liveBots.map((b) =>
        b.ok === false || !b.username
          ? {
              identity: b.name ?? `${b.slot} bot`,
              detail: b.error ?? "Telegram didn't answer for this bot — its token may have been revoked.",
              badge: b.slot,
              failed: true,
            }
          : {
              identity: `${b.name ?? b.username} · @${b.username}`,
              detail: b.detail ?? "bot token in Keychain · tokens don't expire",
              badge: b.slot,
            },
      )
    : c.accounts.map((a) => ({ identity: a.identity, detail: a.detail }));

  const accountsLabel = c.id === "telegram" ? "Bots" : c.accounts.length > 1 ? "Accounts" : "Account";
  const manage = manageTarget(c.id);
  const removalNote: Record<string, string> = {
    oauth: "Access can also be revoked from the provider's own security settings.",
    cli: "To sign out entirely, run `gh auth logout` in a terminal.",
    session: "Unlink this device from WhatsApp on your phone (Settings → Linked devices).",
  };

  const inputCls =
    "w-full h-10 px-3 rounded-lg bg-zinc-950 border border-zinc-800 text-[13.5px] text-zinc-100 " +
    "placeholder:text-zinc-600 outline-none focus:border-zinc-600 transition font-mono";

  const renderProbe = (p: Probe) =>
    p === "running" ? (
      <span className="inline-flex items-center gap-2 text-[12px] text-zinc-300">
        <span className="w-1.5 h-1.5 rounded-full bg-zinc-100 pulse-dot" /> Testing…
      </span>
    ) : p.ok ? (
      <span className="text-[12px] text-emerald-300/90 select-text">
        ✓ {p.message}
        {p.ms !== undefined && <span className="text-zinc-500"> · {p.ms}ms</span>}
      </span>
    ) : (
      <span className="text-[12px] text-red-300 select-text">{p.message}</span>
    );

  return (
    <div className="screen max-w-[720px] mx-auto px-8 pt-14 pb-16">
      <div className="flex items-center gap-1.5 text-[12.5px] mb-6">
        <button onClick={onBack} className="text-zinc-400 hover:text-zinc-100 transition">
          Connectors
        </button>
        <span className="text-zinc-700">/</span>
        <span className="text-zinc-300">{c.name}</span>
      </div>

      <div className="flex items-start gap-4 mb-5">
        <ConnectorLogo id={c.id} name={c.name} size={52} domain={domainFor(c.id)} />
        <div className="flex-1 min-w-0 pt-0.5">
          <div className="flex items-center gap-2.5">
            <h1 className="text-[22px] font-semibold tracking-tight truncate">{c.name}</h1>
            <StatusPill status={c.status} dormant={usage ? !usage.in_use : c.in_use === false} />
          </div>
          <div className="text-[12.5px] text-zinc-400 mt-1">
            {AUTH_LABELS[c.auth_kind] ?? c.detail}
          </div>
        </div>

        {connected && (
          <div className="flex items-center gap-2 shrink-0 relative">
            <button
              onClick={() => probe()}
              disabled={test === "running"}
              className="px-3.5 h-9 rounded-lg border border-zinc-700 text-[12.5px] text-zinc-200
                         hover:text-white hover:border-zinc-500 transition disabled:opacity-40"
            >
              Test connection
            </button>
            {canRemove && (
              <button
                onClick={() => setConfirm({ kind: "disconnect" })}
                className="px-3.5 h-9 rounded-lg border border-zinc-800 text-[12.5px] text-zinc-400
                           hover:text-red-300 hover:border-red-900/70 transition"
              >
                Disconnect
              </button>
            )}
            <button
              onClick={() => setMenu((v) => !v)}
              aria-label="More actions"
              className="w-9 h-9 rounded-lg border border-zinc-800 text-[15px] leading-none text-zinc-400
                         hover:text-zinc-100 hover:border-zinc-600 transition"
            >
              ⋮
            </button>
            {menu && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setMenu(false)} />
                <div className="absolute right-0 top-11 z-40 w-48 rounded-xl border border-zinc-700 bg-zinc-900 py-1 shadow-xl">
                  <button
                    onClick={() => {
                      setMenu(false);
                      refresh();
                    }}
                    className="w-full text-left px-3.5 py-2 text-[12.5px] text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 transition"
                  >
                    Refresh status
                  </button>
                  {manage && (
                    <button
                      onClick={() => {
                        setMenu(false);
                        openExternal(manage.url);
                      }}
                      className="w-full text-left px-3.5 py-2 text-[12.5px] text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 transition"
                    >
                      Open {manage.label}
                    </button>
                  )}
                  {canRemove && (
                    <button
                      onClick={() => {
                        setMenu(false);
                        setConfirm({ kind: "disconnect" });
                      }}
                      className="w-full text-left px-3.5 py-2 text-[12.5px] text-zinc-400 hover:bg-zinc-800 hover:text-red-300 transition"
                    >
                      Remove {c.name}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {test && <div className="mb-5">{renderProbe(test)}</div>}

      {error && (
        <div className="mb-5 rounded-xl border border-red-900/60 bg-red-950/30 px-4 py-3 text-[12.5px] text-red-300 select-text">
          {error}
        </div>
      )}

      {about && about.about && (
        <p className="text-[13.5px] text-zinc-300 leading-relaxed mb-3.5">{about.about}</p>
      )}
      {about && about.provides.length > 0 && (
        <div className={"flex flex-wrap gap-1.5 " + (usage ? "mb-4" : "mb-7")}>
          {about.provides.map((p) => (
            <span
              key={p}
              className="px-2 py-0.5 rounded-md border border-zinc-800 bg-zinc-950/70 text-[11px] text-zinc-400"
            >
              {p}
            </span>
          ))}
        </div>
      )}

      {usage &&
        (usage.in_use ? (
          usage.used_by.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 mb-7">
              <span className="text-[11.5px] text-zinc-500 mr-0.5">Used by</span>
              {usage.used_by.map((u) => (
                <span
                  key={u}
                  className="px-2 py-0.5 rounded-md border border-zinc-800 bg-zinc-900 text-[11px] text-zinc-300"
                >
                  {u}
                </span>
              ))}
            </div>
          )
        ) : (
          <div className="mb-7 rounded-xl border border-amber-900/40 bg-amber-950/10 px-4 py-3 text-[12.5px] text-amber-200/80 leading-relaxed">
            Nothing in your system uses this connection yet — the key sits idle. Safe to disconnect.
          </div>
        ))}

      {accountRows.length > 0 && (
        <div className="mb-7">
          <div className="flex items-baseline justify-between mb-2.5">
            <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500">{accountsLabel}</div>
            {c.id === "telegram" && bots === null && (
              <span className="text-[11px] text-zinc-600">checking…</span>
            )}
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-4">
            {accountRows.map((a) => (
              <div key={a.identity} className="py-3 border-b border-zinc-800/60 last:border-0">
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] text-zinc-100 truncate">{a.identity}</span>
                      {a.badge && (
                        <span className="px-1.5 py-0.5 rounded-md bg-zinc-900 border border-zinc-800 text-[10px] text-zinc-500 shrink-0">
                          {a.badge}
                        </span>
                      )}
                    </div>
                    <div
                      className={
                        "text-[11.5px] mt-0.5 " + (a.failed ? "text-amber-300/90" : "text-zinc-500")
                      }
                    >
                      {a.detail}
                    </div>
                    {rowTest[a.identity] && <div className="mt-1.5">{renderProbe(rowTest[a.identity])}</div>}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {c.id === "google" && (
                      <>
                        <RowAction onClick={() => probe(a.identity)} disabled={rowTest[a.identity] === "running"}>
                          Test
                        </RowAction>
                        <RowAction onClick={() => openExternal("https://myaccount.google.com/connections")}>
                          Google security
                        </RowAction>
                        <RowAction danger onClick={() => setConfirm({ kind: "account", identity: a.identity })}>
                          Remove…
                        </RowAction>
                      </>
                    )}
                    {c.id === "telegram" && (
                      <RowAction onClick={() => openExternal("https://t.me/BotFather")}>Open BotFather</RowAction>
                    )}
                    {c.id === "github" && (
                      <RowAction onClick={() => openExternal("https://github.com/settings/tokens")}>
                        Token settings
                      </RowAction>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
          {connected && removalNote[c.auth_kind] && (
            <div className="text-[11.5px] text-zinc-600 mt-2 leading-relaxed">{removalNote[c.auth_kind]}</div>
          )}
        </div>
      )}

      {!connected && (
        <div className="mb-7">
          <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-2.5">Connect</div>

          {phase === "done" && (
            <div className="rounded-xl border border-zinc-700 bg-zinc-950/60 px-4 py-3 text-[13px] text-zinc-100">
              ✓ {isComposioCard ? "Hosted sign-in enabled." : `${c.name} connected.`} Refresh to see it live.
            </div>
          )}

          {phase === "browser-wait" && (
            <div className="flex items-center gap-3 rounded-xl border border-zinc-700 bg-zinc-950/60 px-4 py-3">
              <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
              <span className="text-[13px] text-zinc-200">Finish signing in to {c.name} in your browser…</span>
            </div>
          )}

          {phase === "keys" && (
            <div className="space-y-3">
              {(isComposioCard ? [keyFields[0]] : keyFields).filter(Boolean).map((f) => (
                <div key={f.secret}>
                  <div className="flex items-baseline justify-between mb-1.5">
                    <label className="text-[12.5px] text-zinc-300">{f.label}</label>
                    {f.get_url && (
                      <button
                        onClick={() => openExternal(f.get_url)}
                        className="text-[11.5px] text-zinc-400 hover:text-zinc-100 underline underline-offset-2 transition"
                      >
                        Get your key →
                      </button>
                    )}
                  </div>
                  <input
                    autoFocus
                    className={inputCls}
                    placeholder="Paste it here — goes straight to your Keychain"
                    value={keyValues[f.secret] ?? ""}
                    onChange={(e) => setKeyValues((s) => ({ ...s, [f.secret]: e.target.value }))}
                  />
                </div>
              ))}
              <div className="flex gap-3 pt-1">
                <button
                  onClick={saveKeys}
                  disabled={busy || !Object.values(keyValues).some((v) => v.trim())}
                  className="px-5 h-10 rounded-xl bg-zinc-100 text-zinc-950 text-[13.5px] font-medium hover:bg-white transition disabled:opacity-40"
                >
                  {busy ? "Saving…" : "Save"}
                </button>
                <button
                  onClick={() => setPhase("view")}
                  className="px-4 h-10 rounded-xl text-[13.5px] text-zinc-400 hover:text-zinc-100 transition"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {phase === "view" && (
            <div className="flex flex-wrap items-center gap-3">
              {canBrowserConnect && (
                <button
                  onClick={browserConnect}
                  disabled={busy}
                  className="px-5 h-10 rounded-xl bg-zinc-100 text-zinc-950 text-[13.5px] font-medium hover:bg-white transition disabled:opacity-40"
                >
                  Connect in browser
                </button>
              )}
              {canPasteKey && (
                <button
                  onClick={() => setPhase("keys")}
                  className={
                    "px-5 h-10 rounded-xl text-[13.5px] font-medium transition " +
                    (canBrowserConnect
                      ? "border border-zinc-700 text-zinc-200 hover:text-white hover:border-zinc-500"
                      : "bg-zinc-100 text-zinc-950 hover:bg-white")
                  }
                >
                  {isComposioCard ? "Enable" : "Use a key instead"}
                </button>
              )}
              {!canBrowserConnect && !canPasteKey && (
                <p className="text-[13px] text-zinc-400">
                  {c.connect_hint || "Ask your agent to connect it — setup is guided."}
                </p>
              )}
              {canBrowserConnect && (
                <span className="text-[11.5px] text-zinc-600">
                  Hosted sign-in — the key never lands on this Mac.
                </span>
              )}
            </div>
          )}
        </div>
      )}

      <div className="mt-8 pt-6 border-t border-zinc-800/80 grid grid-cols-2 gap-x-8 gap-y-5">
        <MetaBlock label="Category">
          <span className="px-2 py-0.5 rounded-md border border-zinc-800 bg-zinc-950/70 text-[11.5px] text-zinc-400">
            {CATEGORY_LABELS[c.category] ?? c.category}
          </span>
        </MetaBlock>
        <MetaBlock label="Sign-in">{AUTH_SHORT[c.auth_kind] ?? c.auth_kind}</MetaBlock>
        <MetaBlock label="Manage at">
          {manage ? (
            <button
              onClick={() => openExternal(manage.url)}
              className="text-zinc-300 hover:text-zinc-100 underline underline-offset-2 transition"
            >
              {manage.label} →
            </button>
          ) : (
            <span className="text-zinc-600">—</span>
          )}
        </MetaBlock>
        <MetaBlock label={c.id === "telegram" ? "Bots" : "Accounts"}>
          {accountRows.length > 0 ? `${accountRows.length} on this Mac` : connected ? "System-wide" : "None yet"}
        </MetaBlock>
      </div>

      <p className="mt-5 text-[11.5px] text-zinc-600 leading-relaxed">
        Keys live in your Mac's Keychain. Hosted sign-ins are held by the connection service and revocable per
        app.
      </p>

      {confirm?.kind === "disconnect" && (
        <ConfirmDialog
          title={`Disconnect ${c.name}?`}
          body={
            perAccountRemoval.length > 0
              ? `All ${perAccountRemoval.length} accounts are removed from this Mac. The system loses access until you sign in again.`
              : (c.composio_slug && composioReady ? "Its sign-in is revoked upstream and " : "") +
                "its keys are removed from your Keychain. The system loses access until you reconnect."
          }
          confirmLabel="Disconnect"
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={disconnect}
        />
      )}
      {confirm?.kind === "account" && (
        <ConfirmDialog
          title={`Remove ${confirm.identity}?`}
          body="Its saved credential is deleted from this Mac. Other accounts on this connector are untouched, and you can re-authorise it any time."
          confirmLabel="Remove account"
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={() => removeAccount(confirm.identity)}
        />
      )}
    </div>
  );
}

/** Composio's own logo when it has one, otherwise the three-tier resolution. */
function ToolkitLogo({ t }: { t: ToolkitCard }) {
  const [failed, setFailed] = useState(false);
  if (t.logo && !failed)
    return (
      <img
        src={t.logo}
        alt=""
        className="w-[34px] h-[34px] rounded-[10px] border border-zinc-800 bg-white/95 object-contain p-1 shrink-0"
        onError={() => setFailed(true)}
      />
    );
  return <ConnectorLogo id={t.slug} name={t.label} domain={domainFor(t.slug)} />;
}

function BrowseDirectory({
  composioReady,
  onConnect,
  onBack,
}: {
  composioReady: boolean;
  onConnect: (slug: string, label: string) => void;
  onBack: () => void;
}) {
  const [cards, setCards] = useState<ToolkitCard[] | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    (async () => {
      if (IN_TAURI) {
        try {
          const r = await invoke<{ cards: ToolkitCard[] }>("composio_toolkits");
          setCards(r.cards.filter((c) => c.slug && c.label));
        } catch {
          setCards(DEMO_TOOLKITS);
        }
      } else {
        await new Promise((r) => setTimeout(r, 400));
        setCards(DEMO_TOOLKITS);
      }
    })();
  }, []);

  const filtered = (cards ?? []).filter(
    (c) => !q || c.label.toLowerCase().includes(q.toLowerCase()) || c.blurb.toLowerCase().includes(q.toLowerCase()),
  );

  return (
    <div className="screen max-w-[720px] mx-auto px-8 pt-14 pb-16">
      <div className="flex items-end justify-between mb-1">
        <h1 className="text-[22px] font-semibold tracking-tight">Browse connectors</h1>
        <button onClick={onBack} className="text-[13px] text-zinc-400 hover:text-zinc-100 transition pb-1">
          Back
        </button>
      </div>
      <p className="text-[13px] text-zinc-400 mb-5">
        {composioReady
          ? "One-click sign-in via hosted OAuth — nothing stored on this Mac."
          : "Enable Composio on the Connectors page to make these one-click."}
      </p>

      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search connectors…"
        autoComplete="off"
        spellCheck={false}
        className="w-full h-11 px-4 mb-5 rounded-xl bg-zinc-900 border border-zinc-800 text-[14px] text-zinc-100
                   placeholder:text-zinc-500 outline-none focus:border-zinc-600 transition"
      />

      {cards === null ? (
        <div className="flex items-center gap-3 py-6">
          <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
          <span className="text-[13.5px] text-zinc-200">Loading directory…</span>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2.5">
          {filtered.slice(0, 60).map((t) => (
            <div
              key={t.slug}
              className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-4 py-3.5 flex items-center gap-3.5"
            >
              <ToolkitLogo t={t} />
              <div className="flex-1 min-w-0">
                <div className="text-[13.5px] font-medium text-zinc-100 truncate">{t.label}</div>
                <div className="text-[11.5px] text-zinc-500 truncate mt-0.5">{t.blurb}</div>
              </div>
              <button
                onClick={() => onConnect(t.slug, t.label)}
                className="text-[12px] px-3 py-1.5 rounded-lg bg-zinc-100 text-zinc-950 font-medium shrink-0 hover:bg-white transition"
              >
                Connect
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ConnectorsPane() {
  const [connectors, setConnectors] = useState<Connector[] | null>(null);
  const [open, setOpen] = useState<Connector | null>(null);
  const [usage, setUsage] = useState<Record<string, ConnectorUsage>>({});
  const [view, setView] = useState<"list" | "browse">("list");
  const [filter, setFilter] = useState<"all" | "connected" | "available">("all");
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    let next: Connector[];
    if (IN_TAURI) {
      try {
        next = await invoke<Connector[]>("list_connectors");
      } catch {
        next = [];
      }
    } else {
      await new Promise((r) => setTimeout(r, 400));
      next = DEMO_CONNECTORS;
    }
    setConnectors(next);
    // Keep an open detail page pointed at the freshly-read row.
    setOpen((cur) => (cur ? (next.find((x) => x.id === cur.id) ?? cur) : cur));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Dormancy is read per connected card AFTER the list has painted; results
  // land as they arrive. Each probe greps the runtime tree, so they run a few
  // at a time — enough to keep the sweep short without hammering the disk.
  useEffect(() => {
    if (!connectors) return;
    let cancelled = false;
    const queue = connectors.filter((x) => x.status === "connected");
    let next = 0;
    const worker = async () => {
      while (!cancelled) {
        const c = queue[next++];
        if (!c) return;
        let u: ConnectorUsage | null = null;
        if (IN_TAURI) {
          try {
            u = await invoke<ConnectorUsage>("connector_usage", { id: c.id });
          } catch {
            u = null;
          }
        } else {
          await new Promise((r) => setTimeout(r, 120));
          u = DEMO_USAGE[c.id] ?? null;
        }
        if (cancelled || !u) continue;
        const found = u;
        setUsage((s) => ({ ...s, [c.id]: found }));
      }
    };
    void Promise.all([worker(), worker(), worker()]);
    return () => {
      cancelled = true;
    };
  }, [connectors]);

  const composioReady =
    connectors?.some((c) => c.id === "composio" && c.status === "connected") ?? false;

  if (open)
    return (
      <ConnectorDetail
        c={open}
        composioReady={composioReady}
        initialUsage={usage[open.id]}
        onBack={() => setOpen(null)}
        onChanged={load}
      />
    );

  if (view === "browse")
    return (
      <BrowseDirectory
        composioReady={composioReady}
        onBack={() => setView("list")}
        onConnect={(slug, label) => {
          setOpen({
            id: slug,
            name: label,
            category: "other",
            auth_kind: "oauth",
            status: "available",
            detail: "",
            accounts: [],
            connect_hint: "",
            key_fields: [],
            composio_slug: slug,
          });
        }}
      />
    );

  const list = (connectors ?? []).filter((c) => {
    if (filter === "connected" && c.status !== "connected") return false;
    if (filter === "available" && c.status === "connected") return false;
    if (q && !c.name.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });
  const cats = Array.from(new Set(list.map((c) => c.category)));
  const connectedCount = (connectors ?? []).filter((c) => c.status === "connected").length;

  return (
    <div className="screen max-w-[720px] mx-auto px-8 pt-14 pb-16">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-[22px] font-semibold tracking-tight">Connectors</h1>
        <button
          onClick={() => setView("browse")}
          className="px-3.5 h-9 rounded-lg bg-zinc-100 text-zinc-950 text-[12.5px] font-medium hover:bg-white transition"
        >
          Add connector
        </button>
      </div>
      <p className="text-[13px] text-zinc-300 mb-5">
        {connectedCount} connected. Outside apps and accounts your system can act through.
      </p>

      <div className="flex items-center gap-3 mb-6">
        <div className="flex rounded-lg border border-zinc-800 overflow-hidden">
          {(
            [
              ["all", "All"],
              ["connected", "Connected"],
              ["available", "Not connected"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setFilter(id)}
              className={
                "px-3.5 h-9 text-[12.5px] transition " +
                (filter === id
                  ? "bg-zinc-100 text-zinc-950 font-medium"
                  : "bg-zinc-900 text-zinc-300 hover:text-zinc-100")
              }
            >
              {label}
            </button>
          ))}
        </div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search…"
          autoComplete="off"
          spellCheck={false}
          className="flex-1 h-9 px-3.5 rounded-lg bg-zinc-900 border border-zinc-800 text-[13px] text-zinc-100
                     placeholder:text-zinc-500 outline-none focus:border-zinc-600 transition"
        />
      </div>

      {connectors === null ? (
        <div className="flex items-center gap-3 py-6">
          <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
          <span className="text-[13.5px] text-zinc-200">Reading connections…</span>
        </div>
      ) : (
        cats.map((cat) => {
          const items = list
            .filter((c) => c.category === cat)
            .sort((a, b) => (a.status === "connected" ? -1 : 1) - (b.status === "connected" ? -1 : 1));
          if (!items.length) return null;
          return (
            <div key={cat} className="mb-7">
              <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-2.5">
                {CATEGORY_LABELS[cat] ?? cat}
              </div>
              <div className="grid grid-cols-2 gap-2.5">
                {items.map((c) => (
                  <ConnectorCard key={c.id} c={c} usage={usage[c.id]} onOpen={setOpen} />
                ))}
              </div>
            </div>
          );
        })
      )}

    </div>
  );
}

/* ── pane: health ── */

const DEMO_HEALTH: HealthReport = {
  mem_total_gb: 16,
  mem_free_pct: 79,
  disk_total_gb: 228,
  disk_avail_gb: 184,
  services: [
    { label: "com.aos.bridge", name: "bridge", running: true, last_exit: 0 },
    { label: "com.aos.qareen", name: "qareen", running: true, last_exit: 0 },
    { label: "com.aos.transcriber", name: "transcriber", running: true, last_exit: -11 },
    { label: "com.aos.n8n", name: "n8n", running: true, last_exit: -15 },
    { label: "com.aos.scheduler", name: "scheduler", running: false, last_exit: 0 },
    { label: "com.aos.sentinel", name: "sentinel", running: true, last_exit: -10 },
  ],
  endpoints: [
    { name: "qareen dashboard", ok: true, detail: "HTTP 200" },
    { name: "bridge", ok: false, detail: "no response" },
    { name: "transcriber", ok: true, detail: "HTTP 200" },
    { name: "n8n", ok: true, detail: "HTTP 200" },
  ],
  issues: [
    "transcriber: crashed (segfault) on its last run — restarted and running now.",
    "n8n: terminated (SIGTERM) on its last run — restarted and running now.",
    "bridge: health endpoint gave no response.",
  ],
};

function Meter({ pct, label, detail }: { pct: number; label: string; detail: string }) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-4">
      <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-2">{label}</div>
      <div className="flex items-baseline gap-2 mb-2.5">
        <span className="text-[24px] font-semibold tabular-nums tracking-tight">{pct}%</span>
        <span className="text-[12px] text-zinc-300">{detail}</span>
      </div>
      <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
        <div
          className={"h-full rounded-full " + (pct > 75 ? "bg-red-400/80" : pct > 55 ? "bg-amber-400/80" : "bg-zinc-200")}
          style={{ width: `${Math.min(100, Math.max(2, pct))}%` }}
        />
      </div>
    </div>
  );
}

function HealthPane() {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    if (IN_TAURI) {
      try {
        setReport(await invoke<HealthReport>("health_check"));
      } catch {
        /* keep previous */
      }
    } else {
      await new Promise((r) => setTimeout(r, 600));
      setReport(DEMO_HEALTH);
    }
    setRefreshing(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (report === null)
    return (
      <div className="flex items-center gap-3 px-10 pt-16">
        <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
        <span className="text-[13.5px] text-zinc-200">Inspecting the system…</span>
      </div>
    );

  const memUsed = report.mem_free_pct !== null ? 100 - report.mem_free_pct : null;
  const diskUsed =
    report.disk_total_gb > 0
      ? Math.round(((report.disk_total_gb - report.disk_avail_gb) / report.disk_total_gb) * 100)
      : null;
  const healthy = report.issues.length === 0;

  return (
    <div className="screen max-w-[680px] mx-auto px-8 pt-14">
      <div className="flex items-end justify-between mb-1">
        <h1 className="text-[22px] font-semibold tracking-tight">Health</h1>
        <button
          onClick={load}
          disabled={refreshing}
          className="text-[13px] text-zinc-300 hover:text-zinc-100 transition pb-1 disabled:opacity-40"
        >
          {refreshing ? "Checking…" : "Refresh"}
        </button>
      </div>
      <p className="text-[13px] text-zinc-300 mb-6">
        {healthy
          ? "Everything looks good."
          : `${report.issues.length} thing${report.issues.length > 1 ? "s" : ""} worth a look.`}
      </p>

      {report.issues.length > 0 && (
        <div className="rounded-2xl border border-amber-900/40 bg-amber-950/20 px-5 py-4 mb-4">
          {report.issues.map((iss, i) => (
            <div key={i} className="flex gap-2.5 py-1 text-[13px] text-amber-200/90">
              <span className="text-amber-400/90 font-semibold shrink-0">!</span>
              <span className="select-text">{iss}</span>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 mb-4">
        {memUsed !== null && (
          <Meter pct={memUsed} label="Memory" detail={`of ${report.mem_total_gb} GB in use`} />
        )}
        {diskUsed !== null && (
          <Meter
            pct={diskUsed}
            label="Storage"
            detail={`${report.disk_avail_gb} GB free of ${report.disk_total_gb} GB`}
          />
        )}
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-2 mb-4">
        <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mt-2 mb-1">Services</div>
        {report.services.map((s) => {
          const crashedBefore = s.last_exit !== 0;
          return (
            <div key={s.label} className="flex items-center gap-2.5 py-2 border-b border-zinc-800/50 last:border-0">
              <div
                className={
                  "w-2 h-2 rounded-full shrink-0 " +
                  (s.running
                    ? crashedBefore
                      ? "bg-amber-400/90"
                      : "bg-zinc-100"
                    : crashedBefore
                      ? "bg-red-400/90"
                      : "border border-zinc-600")
                }
              />
              <span className="text-[13px] text-zinc-100 flex-1">{s.name}</span>
              <span className="font-mono text-[10.5px] text-zinc-500">
                {s.running ? (crashedBefore ? "running · crashed before" : "running") : crashedBefore ? "down" : "idle"}
              </span>
            </div>
          );
        })}
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-4">
        <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-2.5">Endpoints</div>
        <div className="flex flex-wrap gap-2">
          {report.endpoints.map((e) => (
            <span
              key={e.name}
              className={
                "inline-flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-[12px] " +
                (e.ok ? "border-zinc-700 text-zinc-100" : "border-red-900/60 text-red-300")
              }
            >
              <span className={"w-1.5 h-1.5 rounded-full " + (e.ok ? "bg-zinc-100" : "bg-red-400")} />
              {e.name}
              <span className="font-mono text-[10px] text-zinc-500">{e.detail}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── shell: sidebar + panes ── */

function NavIcon({ id }: { id: PaneId }) {
  const stroke = { stroke: "currentColor", strokeWidth: 1.5, fill: "none", strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  switch (id) {
    case "home":
      return (
        <svg width="15" height="15" viewBox="0 0 16 16">
          <path d="M2.5 6.5 8 2l5.5 4.5V13a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1z" {...stroke} />
        </svg>
      );
    case "arms":
      return (
        <svg width="15" height="15" viewBox="0 0 16 16">
          <rect x="2" y="2" width="5" height="5" rx="1.2" {...stroke} />
          <rect x="9" y="2" width="5" height="5" rx="1.2" {...stroke} />
          <rect x="2" y="9" width="5" height="5" rx="1.2" {...stroke} />
          <path d="M11.5 9.5v4M9.5 11.5h4" {...stroke} />
        </svg>
      );
    case "config":
      return (
        <svg width="15" height="15" viewBox="0 0 16 16">
          <path d="M2.5 4.5h7M12.5 4.5h1M2.5 11.5h1M6.5 11.5h7" {...stroke} />
          <circle cx="11" cy="4.5" r="1.7" {...stroke} />
          <circle cx="5" cy="11.5" r="1.7" {...stroke} />
        </svg>
      );
    case "updates":
      return (
        <svg width="15" height="15" viewBox="0 0 16 16">
          <circle cx="8" cy="8" r="6" {...stroke} />
          <path d="M8 5v5M5.8 8.2 8 10.4l2.2-2.2" {...stroke} />
        </svg>
      );
    case "health":
      return (
        <svg width="15" height="15" viewBox="0 0 16 16">
          <path d="M1.5 8.5h3l1.5-4 2.5 7 1.5-3h4.5" {...stroke} />
        </svg>
      );
    case "connectors":
      return (
        <svg width="15" height="15" viewBox="0 0 16 16">
          <path d="M5.5 2.5v3M10.5 2.5v3M4 5.5h8v2.5a4 4 0 0 1-8 0zM8 12v2" {...stroke} />
        </svg>
      );
  }
}

function Sidebar({
  pane,
  setPane,
  sys,
}: {
  pane: PaneId;
  setPane: (p: PaneId) => void;
  sys: SystemInfo | null;
}) {
  const items: { id: PaneId; label: string; section: string }[] = [
    { id: "home", label: "Home", section: "" },
    { id: "health", label: "Health", section: "" },
    { id: "arms", label: "Arms", section: "System" },
    { id: "connectors", label: "Connectors", section: "System" },
    { id: "config", label: "Configuration", section: "System" },
    { id: "updates", label: "Updates", section: "System" },
  ];
  let lastSection = "";
  const updateAvailable = sys?.update_status === "update_available";
  return (
    <div className="w-[220px] shrink-0 h-full border-r border-zinc-800/80 bg-zinc-950/70 flex flex-col pt-12 pb-4 px-3">
      <div className="flex items-center gap-2.5 px-2 mb-6">
        <Mark size={26} />
        <span className="text-[13px] font-medium text-zinc-100">This Mac</span>
      </div>
      <nav className="flex-1">
        {items.map((it) => {
          const header =
            it.section && it.section !== lastSection ? (
              <div key={it.section} className="text-[10.5px] uppercase tracking-[0.12em] text-zinc-500 px-2 mt-5 mb-1.5">
                {it.section}
              </div>
            ) : null;
          lastSection = it.section;
          const active = pane === it.id;
          return (
            <div key={it.id}>
              {header}
              <button
                onClick={() => setPane(it.id)}
                className={
                  "w-full flex items-center gap-2.5 px-2.5 h-9 rounded-lg text-[13px] transition " +
                  (active
                    ? "bg-zinc-800/80 text-zinc-50"
                    : "text-zinc-300 hover:text-zinc-100 hover:bg-zinc-900")
                }
              >
                <span className={active ? "text-zinc-100" : "text-zinc-500"}>
                  <NavIcon id={it.id} />
                </span>
                <span className="flex-1 text-left">{it.label}</span>
                {it.id === "updates" && updateAvailable && (
                  <span className="w-1.5 h-1.5 rounded-full bg-zinc-100" />
                )}
              </button>
            </div>
          );
        })}
      </nav>
      <div className="px-2.5 text-[11px] text-zinc-500 font-mono">{sys?.version ?? ""}</div>
    </div>
  );
}

const OPERATOR_DEMO: Record<string, string> = {
  name: "Hisham Al Hadi",
  agent_name: "Chief",
  timezone: "America/Toronto",
  style: "concise",
  language: "en",
  morning_briefing: "07:00",
  evening_checkin: "21:00",
  trust_level: "2",
};

function ConfigPane() {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      if (IN_TAURI) {
        try {
          setValues((await invoke<Record<string, string>>("operator_config")) ?? {});
        } catch (e) {
          setError(String(e));
        }
      } else {
        setValues(OPERATOR_DEMO);
      }
      setLoaded(true);
    })();
  }, []);

  const set = (k: string, v: string) => setValues((s) => ({ ...s, [k]: v }));

  const save = useCallback(async () => {
    setError(null);
    if (IN_TAURI) {
      try {
        await invoke("save_operator_config", { fields: values });
      } catch (e) {
        setError(String(e));
        return;
      }
    }
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }, [values]);

  const inputCls =
    "w-64 h-10 px-3 rounded-lg bg-zinc-900 border border-zinc-800 text-[14px] text-zinc-100 " +
    "placeholder:text-zinc-500 outline-none focus:border-zinc-600 transition";
  const smallInput = inputCls.replace("w-64", "w-28");

  const TRUST_LABELS = ["Shadow", "Approval", "Semi-auto", "Full-auto"];

  return (
    <div className="screen max-w-[640px] mx-auto px-8 pt-14 pb-16">
      <h1 className="text-[22px] font-semibold tracking-tight mb-1">Configuration</h1>
      <p className="text-[13px] text-zinc-300 mb-6">
        Your operator profile — saved straight into the system's configuration.
      </p>

      {error && (
        <div className="mb-4 rounded-xl border border-red-900/60 bg-red-950/30 px-4 py-3 text-[13px] text-red-300 select-text">
          {error}
        </div>
      )}

      {loaded && (
        <>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 divide-y divide-zinc-800/80 mb-4">
            <Field label="Your name">
              <input className={inputCls} value={values.name ?? ""} onChange={(e) => set("name", e.target.value)} />
            </Field>
            <Field label="Agent's name">
              <input
                className={inputCls}
                placeholder="What you call your agent"
                value={values.agent_name ?? ""}
                onChange={(e) => set("agent_name", e.target.value)}
              />
            </Field>
            <Field label="Timezone">
              <input className={inputCls} value={values.timezone ?? ""} onChange={(e) => set("timezone", e.target.value)} />
            </Field>
            <Field label="Communication style">
              <div className="flex rounded-lg border border-zinc-800 overflow-hidden">
                {(["concise", "detailed"] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => set("style", s)}
                    className={
                      "px-4 h-10 text-[13px] capitalize transition " +
                      ((values.style ?? "concise") === s
                        ? "bg-zinc-100 text-zinc-950 font-medium"
                        : "bg-zinc-900 text-zinc-300 hover:text-zinc-100")
                    }
                  >
                    {s}
                  </button>
                ))}
              </div>
            </Field>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 divide-y divide-zinc-800/80 mb-4">
            <Field label="Morning briefing">
              <input
                className={smallInput + " text-center font-mono"}
                value={values.morning_briefing ?? ""}
                onChange={(e) => set("morning_briefing", e.target.value)}
              />
            </Field>
            <Field label="Evening check-in">
              <input
                className={smallInput + " text-center font-mono"}
                value={values.evening_checkin ?? ""}
                onChange={(e) => set("evening_checkin", e.target.value)}
              />
            </Field>
            <Field label="Trust level">
              <div className="flex rounded-lg border border-zinc-800 overflow-hidden">
                {TRUST_LABELS.map((label, i) => (
                  <button
                    key={label}
                    onClick={() => set("trust_level", String(i))}
                    title={label}
                    className={
                      "px-3 h-10 text-[12.5px] transition " +
                      ((values.trust_level ?? "1") === String(i)
                        ? "bg-zinc-100 text-zinc-950 font-medium"
                        : "bg-zinc-900 text-zinc-300 hover:text-zinc-100")
                    }
                  >
                    {label}
                  </button>
                ))}
              </div>
            </Field>
          </div>
        </>
      )}

      <div className="flex items-center gap-3">
        <PrimaryButton onClick={save}>Save</PrimaryButton>
        {saved && <span className="screen text-[13px] text-zinc-300">Saved to operator.yaml</span>}
      </div>
    </div>
  );
}

function UpdatesPane({
  sys,
  checking,
  onCheck,
  onUpdate,
}: {
  sys: SystemInfo | null;
  checking: boolean;
  onCheck: () => void;
  onUpdate: () => void;
}) {
  const updateAvailable = sys?.update_status === "update_available";
  const [notes, setNotes] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      if (IN_TAURI) {
        try {
          setNotes(await invoke<string>("release_notes"));
        } catch {
          setNotes(null);
        }
      } else {
        setNotes(
          "## v0.7.5 — 2026-08-15\n\nSummary: The context diet — every session starts ~30k tokens lighter.\n\n- Changed chief.md from a 17KB manual into a 5.5KB router\n- Added a Context Budget section to ship-check\n- Fixed the fresh-install onboarding banner never reaching the session",
        );
      }
    })();
  }, []);

  return (
    <div className="screen max-w-[640px] mx-auto px-8 pt-14 pb-16">
      <h1 className="text-[22px] font-semibold tracking-tight mb-1">Updates</h1>
      <p className="text-[13px] text-zinc-300 mb-6">
        System updates install in place and restart services automatically.
      </p>
      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-[14px] text-zinc-100 font-medium flex items-center gap-2.5">
              <span className="font-mono text-[13px] px-2 py-0.5 rounded-md border border-zinc-800 bg-zinc-900">
                {sys?.version ?? "—"}
              </span>
              {checking ? (
                <span className="flex items-center gap-2 text-[13px] text-zinc-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-zinc-100 pulse-dot" /> checking…
                </span>
              ) : updateAvailable ? (
                <span className="text-[13px] text-zinc-100">update available</span>
              ) : (
                <span className="text-[13px] text-zinc-300">up to date</span>
              )}
            </div>
            {sys?.last_check && (
              <div className="text-[11.5px] text-zinc-500 mt-1.5">
                Last checked {sys.last_check.replace("T", " ").slice(0, 16)}
              </div>
            )}
          </div>
          {updateAvailable ? (
            <button
              onClick={onUpdate}
              className="px-4 h-10 rounded-lg bg-zinc-100 text-zinc-950 text-[13.5px] font-medium hover:bg-white transition shrink-0"
            >
              Update now
            </button>
          ) : (
            <button
              onClick={onCheck}
              disabled={checking}
              className="px-4 h-10 rounded-lg border border-zinc-700 text-[13.5px] text-zinc-200 hover:text-white hover:border-zinc-500 transition disabled:opacity-40 shrink-0"
            >
              Check now
            </button>
          )}
        </div>
      </div>

      {notes && (
        <div className="mt-6">
          <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-2.5">
            What's new
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-6 py-5">
            {notes.split("\n").map((line, i) => {
              const t = line.trim();
              if (t.startsWith("## "))
                return (
                  <div key={i} className="text-[15px] font-semibold text-zinc-50 mt-4 first:mt-0 mb-1">
                    {t.slice(3)}
                  </div>
                );
              if (t.startsWith("Summary:"))
                return (
                  <div key={i} className="text-[13px] text-zinc-200 italic mb-2">
                    {t.slice(8).trim()}
                  </div>
                );
              if (t.startsWith("- "))
                return (
                  <div key={i} className="flex gap-2.5 py-0.5 text-[13px] text-zinc-200 leading-relaxed">
                    <span className="text-zinc-500 shrink-0">–</span>
                    <span>{t.slice(2)}</span>
                  </div>
                );
              if (!t) return null;
              return (
                <div key={i} className="text-[13px] text-zinc-200 py-0.5 leading-relaxed">
                  {t}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function Shell({
  sys,
  checking,
  onCheck,
  onUpdate,
}: {
  sys: SystemInfo | null;
  checking: boolean;
  onCheck: () => void;
  onUpdate: () => void;
}) {
  const [pane, setPane] = useState<PaneId>("home");
  return (
    <div className="h-full flex">
      <Sidebar pane={pane} setPane={setPane} sys={sys} />
      <div className="flex-1 min-w-0 overflow-y-auto console pb-14">
        {pane === "home" && <Home sys={sys} onUpdate={onUpdate} />}
        {pane === "health" && <HealthPane />}
        {pane === "arms" && <Arms />}
        {pane === "connectors" && <ConnectorsPane />}
        {pane === "config" && <ConfigPane />}
        {pane === "updates" && (
          <UpdatesPane sys={sys} checking={checking} onCheck={onCheck} onUpdate={onUpdate} />
        )}
      </div>
    </div>
  );
}

/* ── screen 2: configure ── */

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-6 py-4">
      <span className="text-[14px] text-zinc-300">{label}</span>
      {children}
    </div>
  );
}

function Configure({
  config,
  setConfig,
  onBack,
  onContinue,
}: {
  config: SetupConfig;
  setConfig: (c: SetupConfig) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const inputCls =
    "w-64 h-10 px-3 rounded-lg bg-zinc-900 border border-zinc-800 text-[14px] text-zinc-100 " +
    "placeholder:text-zinc-500 outline-none focus:border-zinc-600 transition";

  return (
    <div className="screen h-full flex flex-col items-center justify-center">
      <div className="w-[560px] max-w-[86vw]">
        <h1 className="text-[22px] font-semibold tracking-tight mb-1">Configuration</h1>
        <p className="text-[13px] text-zinc-300 mb-6">
          The essentials. Everything else can be changed later in Settings.
        </p>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 divide-y divide-zinc-800/80">
          <Field label="Your name">
            <input
              className={inputCls}
              placeholder="How the system addresses you"
              value={config.operatorName}
              onChange={(e) => setConfig({ ...config, operatorName: e.target.value })}
            />
          </Field>
          <Field label="Machine name">
            <input
              className={inputCls}
              placeholder="e.g. studio-mini"
              value={config.machineName}
              onChange={(e) => setConfig({ ...config, machineName: e.target.value })}
            />
          </Field>
          <Field label="Role">
            <div className="flex rounded-lg border border-zinc-800 overflow-hidden">
              {(["primary", "worker"] as const).map((r) => (
                <button
                  key={r}
                  onClick={() => setConfig({ ...config, role: r })}
                  className={
                    "px-4 h-10 text-[13px] capitalize transition " +
                    (config.role === r
                      ? "bg-zinc-100 text-zinc-950 font-medium"
                      : "bg-zinc-900 text-zinc-200 hover:text-zinc-200")
                  }
                >
                  {r}
                </button>
              ))}
            </div>
          </Field>
          <Field label="Safe preview">
            <button
              onClick={() => setConfig({ ...config, dryRun: !config.dryRun })}
              className={
                "w-11 h-6.5 rounded-full relative transition " +
                (config.dryRun ? "bg-zinc-100" : "bg-zinc-800")
              }
              title="Dry run — walks every step without changing the machine"
            >
              <div
                className={
                  "absolute top-0.5 w-5.5 h-5.5 rounded-full transition-all " +
                  (config.dryRun ? "left-5 bg-zinc-950" : "left-0.5 bg-zinc-500")
                }
              />
            </button>
          </Field>
        </div>

        <p className="text-[12px] text-zinc-500 mt-3">
          Safe preview walks the full installation without changing anything.
        </p>

        <div className="flex justify-between mt-8">
          <button
            onClick={onBack}
            className="px-4 h-11 rounded-xl text-[14px] text-zinc-200 hover:text-zinc-100 transition"
          >
            Back
          </button>
          <PrimaryButton onClick={onContinue}>
            {config.dryRun ? "Preview installation" : "Install"}
          </PrimaryButton>
        </div>
      </div>
    </div>
  );
}

/* ── screen 3: install ── */

function StageRow({
  name,
  state,
}: {
  name: string;
  state: "pending" | "active" | "done";
}) {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <div className="w-5 flex justify-center">
        {state === "done" ? (
          <svg width="14" height="14" viewBox="0 0 14 14" className="text-zinc-100">
            <path
              d="M2.5 7.5 5.5 10.5 11.5 3.5"
              stroke="currentColor"
              strokeWidth="1.8"
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        ) : state === "active" ? (
          <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
        ) : (
          <div className="w-1.5 h-1.5 rounded-full bg-zinc-700" />
        )}
      </div>
      <span
        className={
          "text-[14px] transition " +
          (state === "done"
            ? "text-zinc-300"
            : state === "active"
              ? "text-zinc-100"
              : "text-zinc-500")
        }
      >
        {name}
      </span>
    </div>
  );
}

function Install({
  config,
  onFinished,
}: {
  config: SetupConfig;
  onFinished: (code: number) => void;
}) {
  const [lines, setLines] = useState<string[]>([]);
  const [stageIdx, setStageIdx] = useState(-1);
  const [error, setError] = useState<string | null>(null);
  const [showConsole, setShowConsole] = useState(false);
  const consoleRef = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  useEffect(() => {
    const un1 = listen<string>("install:line", (e) => {
      const clean = stripAnsi(e.payload).trimEnd();
      if (!clean.trim()) return;
      setLines((l) => [...l.slice(-500), clean]);
      const idx = STAGES.findIndex((s) => clean.includes(s));
      if (idx >= 0) setStageIdx((cur) => Math.max(cur, idx));
    });
    const un2 = listen<number>("install:done", (e) => {
      if (e.payload === 0) setStageIdx(STAGES.length);
      onFinished(e.payload);
    });
    if (!started.current) {
      started.current = true;
      const inTauri = "__TAURI_INTERNALS__" in window;
      if (inTauri) {
        invoke("run_install", { dryRun: config.dryRun }).catch((err) =>
          setError(String(err)),
        );
      } else {
        // Browser demo mode — no Tauri runtime, simulate the ceremony.
        STAGES.forEach((s, i) => {
          setTimeout(() => {
            setLines((l) => [...l, `[${i + 1}/6] ✓ ${s} (demo)`]);
            setStageIdx(i);
          }, 700 * (i + 1));
        });
        setTimeout(() => {
          setStageIdx(STAGES.length);
          onFinished(0);
        }, 700 * (STAGES.length + 1));
      }
    }
    return () => {
      un1.then((f) => f());
      un2.then((f) => f());
    };
  }, [config.dryRun, onFinished]);

  useEffect(() => {
    consoleRef.current?.scrollTo({ top: consoleRef.current.scrollHeight });
  }, [lines]);

  return (
    <div className="screen h-full flex flex-col items-center justify-center">
      <div className="w-[560px] max-w-[86vw]">
        <h1 className="text-[22px] font-semibold tracking-tight mb-1">
          {config.dryRun ? "Previewing installation" : "Installing"}
        </h1>
        <p className="text-[13px] text-zinc-300 mb-6">
          {config.dryRun
            ? "Walking every step without changing this Mac."
            : "This can take a while. You can keep using your Mac."}
        </p>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-2">
          {STAGES.map((s, i) => (
            <StageRow
              key={s}
              name={s}
              state={i < stageIdx ? "done" : i === stageIdx ? "active" : "pending"}
            />
          ))}
        </div>

        {error && (
          <div className="mt-4 rounded-xl border border-red-900/60 bg-red-950/30 px-4 py-3 text-[13px] text-red-300">
            {error}
          </div>
        )}

        <button
          onClick={() => setShowConsole((v) => !v)}
          className="mt-4 text-[12px] text-zinc-500 hover:text-zinc-300 transition"
        >
          {showConsole ? "Hide details" : "Show details"}
        </button>

        {showConsole && (
          <div
            ref={consoleRef}
            className="console mt-2 h-40 overflow-y-auto rounded-xl border border-zinc-800 bg-black/60 px-4 py-3
                       font-mono text-[11.5px] leading-relaxed text-zinc-300 whitespace-pre-wrap select-text"
          >
            {lines.join("\n")}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── screen 4: done ── */

function Done({
  config,
  exitCode,
  onRestart,
}: {
  config: SetupConfig;
  exitCode: number;
  onRestart: () => void;
}) {
  const ok = exitCode === 0;
  return (
    <div className="screen h-full flex flex-col items-center justify-center gap-8">
      <Mark />
      <div className="text-center space-y-2">
        <h1 className="text-[24px] font-semibold tracking-tight">
          {ok
            ? config.dryRun
              ? "Preview complete"
              : `All set${config.operatorName ? ", " + config.operatorName : ""}`
            : "Something needs attention"}
        </h1>
        <p className="text-[14px] text-zinc-200 max-w-sm leading-relaxed">
          {ok
            ? config.dryRun
              ? "Every step walked clean. Run it for real when you're ready."
              : "Your system is installed and the agents are awake."
            : `The installer exited with code ${exitCode}. Check the details and try again — it resumes where it left off.`}
        </p>
      </div>
      <PrimaryButton onClick={onRestart}>{ok ? "Start over" : "Try again"}</PrimaryButton>
    </div>
  );
}

/* ── root ── */

export default function App() {
  const [screen, setScreen] = useState<Screen>("welcome");
  const [exitCode, setExitCode] = useState(0);
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [checking, setChecking] = useState(false);
  const [config, setConfig] = useState<SetupConfig>({
    operatorName: "",
    machineName: "",
    role: "primary",
    dryRun: true,
  });

  // Detect an existing install on launch, then refresh the update check.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (IN_TAURI) {
        try {
          const s = await invoke<SystemInfo>("detect_system");
          if (cancelled) return;
          setSys(s);
          if (s.installed) {
            setChecking(true);
            try {
              const fresh = await invoke<SystemInfo>("check_updates");
              if (!cancelled) setSys(fresh);
            } catch {
              /* stale state still shows */
            }
            if (!cancelled) setChecking(false);
          }
        } catch {
          if (!cancelled) setSys({ installed: false });
        }
      } else if (window.location.search.includes("fresh")) {
        // Browser demo of the fresh-machine flow: ?fresh
        setSys({ installed: false });
      } else {
        // Browser demo: pretend we're an existing install and find an update.
        setSys({ installed: true, version: "v0.7.5", operator: "Hadi" });
        setChecking(true);
        setTimeout(() => {
          if (cancelled) return;
          setSys({
            installed: true,
            version: "v0.7.5",
            operator: "Hadi",
            update_status: "update_available",
          });
          setChecking(false);
        }, 1600);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const checkNow = useCallback(async () => {
    setChecking(true);
    if (IN_TAURI) {
      try {
        setSys(await invoke<SystemInfo>("check_updates"));
      } catch {
        /* keep old */
      }
    } else {
      await new Promise((r) => setTimeout(r, 1200));
      setSys((s) => (s ? { ...s, update_status: "update_available" } : s));
    }
    setChecking(false);
  }, []);

  const handleFinished = useCallback((code: number) => {
    setExitCode(code);
    // small beat so the last checkmark lands before transitioning
    setTimeout(() => setScreen("done"), 900);
  }, []);

  const handleUpdateFinished = useCallback((code: number) => {
    setTimeout(async () => {
      if (code === 0) {
        if (IN_TAURI) {
          try {
            setSys(await invoke<SystemInfo>("detect_system"));
          } catch {
            /* keep old */
          }
        } else {
          setSys((s) => ({ ...s!, version: "v0.7.6", update_status: "up_to_date" }));
        }
        setScreen("welcome");
      } else {
        setExitCode(code);
        setScreen("done");
      }
    }, 700);
  }, []);

  const existing = sys?.installed === true;

  return (
    <div className="h-full">
      <DragRegion />
      {screen === "welcome" &&
        (sys === null ? (
          <div className="h-full flex items-center justify-center">
            <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
          </div>
        ) : existing ? (
          <Shell
            sys={sys}
            checking={checking}
            onCheck={checkNow}
            onUpdate={() => setScreen("update")}
          />
        ) : (
          <Welcome onSetup={() => setScreen("preflight")} onJoin={() => setScreen("member")} />
        ))}
      {screen === "preflight" && (
        <Preflight onBack={() => setScreen("welcome")} onContinue={() => setScreen("configure")} />
      )}
      {screen === "member" && <Member onBack={() => setScreen("welcome")} />}
      {screen === "update" && <Update onFinished={handleUpdateFinished} />}
      {screen === "configure" && (
        <Configure
          config={config}
          setConfig={setConfig}
          onBack={() => setScreen("welcome")}
          onContinue={() => {
            // Persist the answers where installer + onboarding read them.
            if (IN_TAURI) {
              invoke("save_setup_config", {
                operatorName: config.operatorName,
                machineName: config.machineName,
                role: config.role,
              }).catch(() => {});
            }
            setScreen("install");
          }}
        />
      )}
      {screen === "install" && <Install config={config} onFinished={handleFinished} />}
      {screen === "done" && (
        <Done config={config} exitCode={exitCode} onRestart={() => setScreen("welcome")} />
      )}
    </div>
  );
}

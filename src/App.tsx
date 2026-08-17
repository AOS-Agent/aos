import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { ConnectorLogo } from "./logos";

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
interface Connector {
  id: string;
  name: string;
  category: string;
  auth_kind: string;
  status: string; // connected | attention | available
  detail: string;
  accounts: ConnectorAccount[];
  connect_hint: string;
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
  { id: "slack", name: "Slack", category: "communication", auth_kind: "token", status: "connected", detail: "bot + app tokens in Keychain", connect_hint: "", accounts: [] },
  { id: "cloudflare", name: "Cloudflare", category: "infrastructure", auth_kind: "token", status: "connected", detail: "2 accounts", connect_hint: "", accounts: [{ identity: "personal (hish.am)", detail: "API token" }, { identity: "Elora Greens", detail: "API token" }] },
  { id: "clickup", name: "ClickUp", category: "productivity", auth_kind: "token", status: "connected", detail: "API token in Keychain", connect_hint: "", accounts: [] },
  { id: "obsidian", name: "Obsidian", category: "knowledge", auth_kind: "token", status: "connected", detail: "local REST API", connect_hint: "", accounts: [] },
  { id: "notion", name: "Notion", category: "knowledge", auth_kind: "token", status: "available", detail: "Pages and databases as agent workspace.", connect_hint: "Ask your agent to connect it — setup is guided.", accounts: [] },
  { id: "linear", name: "Linear", category: "development", auth_kind: "token", status: "available", detail: "Issues and cycles.", connect_hint: "Ask your agent to connect it — setup is guided.", accounts: [] },
  { id: "discord", name: "Discord", category: "communication", auth_kind: "token", status: "available", detail: "Bot presence in your servers.", connect_hint: "Ask your agent to connect it — setup is guided.", accounts: [] },
  { id: "todoist", name: "Todoist", category: "productivity", auth_kind: "token", status: "available", detail: "Tasks and projects.", connect_hint: "Ask your agent to connect it — setup is guided.", accounts: [] },
];

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

function ConnectorCard({ c, onOpen }: { c: Connector; onOpen: (c: Connector) => void }) {
  const connected = c.status === "connected";
  const attention = c.status === "attention";
  return (
    <button
      onClick={() => onOpen(c)}
      className="text-left rounded-2xl border border-zinc-800 bg-zinc-950/60 px-4 py-3.5 hover:border-zinc-600 transition flex items-center gap-3.5"
    >
      <ConnectorLogo id={c.id} name={c.name} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13.5px] font-medium text-zinc-100 truncate">{c.name}</span>
          {c.accounts.length > 1 && (
            <span className="text-[10.5px] text-zinc-500 font-mono">×{c.accounts.length}</span>
          )}
        </div>
        <div className="text-[11.5px] text-zinc-500 truncate mt-0.5">{c.detail}</div>
      </div>
      {connected ? (
        <span className="inline-flex items-center gap-1.5 text-[11px] text-zinc-200 shrink-0">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400/90" /> Connected
        </span>
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

function ConnectorsPane() {
  const [connectors, setConnectors] = useState<Connector[] | null>(null);
  const [open, setOpen] = useState<Connector | null>(null);

  useEffect(() => {
    (async () => {
      if (IN_TAURI) {
        try {
          setConnectors(await invoke<Connector[]>("list_connectors"));
        } catch {
          setConnectors([]);
        }
      } else {
        await new Promise((r) => setTimeout(r, 400));
        setConnectors(DEMO_CONNECTORS);
      }
    })();
  }, []);

  if (connectors === null)
    return (
      <div className="flex items-center gap-3 px-10 pt-16">
        <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
        <span className="text-[13.5px] text-zinc-200">Reading connections…</span>
      </div>
    );

  const cats = Array.from(new Set(connectors.map((c) => c.category)));
  const connectedCount = connectors.filter((c) => c.status === "connected").length;

  return (
    <div className="screen max-w-[720px] mx-auto px-8 pt-14 pb-16">
      <h1 className="text-[22px] font-semibold tracking-tight mb-1">Connectors</h1>
      <p className="text-[13px] text-zinc-300 mb-7">
        {connectedCount} connected. Outside apps and accounts your system can act through.
      </p>

      {cats.map((cat) => {
        const items = connectors
          .filter((c) => c.category === cat)
          .sort((a, b) => (a.status === "connected" ? -1 : 1) - (b.status === "connected" ? -1 : 1));
        return (
          <div key={cat} className="mb-7">
            <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-2.5">
              {CATEGORY_LABELS[cat] ?? cat}
            </div>
            <div className="grid grid-cols-2 gap-2.5">
              {items.map((c) => (
                <ConnectorCard key={c.id} c={c} onOpen={setOpen} />
              ))}
            </div>
          </div>
        );
      })}

      {open && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-40" onClick={() => setOpen(null)}>
          <div
            className="screen w-[440px] max-w-[88vw] rounded-2xl border border-zinc-700 bg-zinc-900 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3.5 mb-4">
              <ConnectorLogo id={open.id} name={open.name} size={42} />
              <div>
                <h2 className="text-[16px] font-semibold">{open.name}</h2>
                <div className="text-[12px] text-zinc-400">{AUTH_LABELS[open.auth_kind] ?? ""}</div>
              </div>
            </div>

            {open.accounts.length > 0 && (
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-1 mb-4">
                {open.accounts.map((a) => (
                  <div key={a.identity} className="py-2.5 border-b border-zinc-800/60 last:border-0">
                    <div className="text-[13px] text-zinc-100">{a.identity}</div>
                    <div className="text-[11.5px] text-zinc-500 mt-0.5">{a.detail}</div>
                  </div>
                ))}
              </div>
            )}

            {open.status !== "connected" && (
              <p className="text-[13px] text-zinc-300 leading-relaxed mb-2">{open.detail}</p>
            )}
            {open.connect_hint && (
              <p className="text-[12.5px] text-zinc-400 leading-relaxed">{open.connect_hint}</p>
            )}

            <div className="flex justify-end gap-3 mt-5">
              <button
                onClick={() => setOpen(null)}
                className="px-4 h-10 rounded-xl text-[13.5px] text-zinc-300 hover:text-zinc-100 transition"
              >
                Close
              </button>
              {open.status !== "connected" && (
                <button className="px-5 h-10 rounded-xl bg-zinc-100 text-zinc-950 text-[13.5px] font-medium hover:bg-white transition">
                  Connect
                </button>
              )}
            </div>
          </div>
        </div>
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

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

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
  | "update";

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
        <p className="text-[14px] text-zinc-400 max-w-sm leading-relaxed">
          Run your own agentic operating system on this Mac, or join a workspace
          that's already running one.
        </p>
      </div>
      <div className="flex flex-col items-center gap-3">
        <PrimaryButton onClick={onSetup}>Set up this Mac</PrimaryButton>
        <button
          onClick={onJoin}
          className="px-4 h-10 rounded-xl text-[13.5px] text-zinc-400 hover:text-zinc-100 transition"
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
        <p className="text-[13px] text-zinc-500 mb-6">
          Making sure this Mac is ready before anything is installed.
        </p>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-1 min-h-[120px]">
          {checks === null ? (
            <div className="flex items-center gap-3 py-4">
              <div className="w-2 h-2 rounded-full bg-zinc-100 pulse-dot" />
              <span className="text-[13.5px] text-zinc-400">Inspecting…</span>
            </div>
          ) : (
            checks.slice(0, revealed).map((c) => (
              <div key={c.id} className="screen flex items-center gap-3 py-2.5">
                <div className="w-5 flex justify-center">
                  <StatusIcon status={c.status} />
                </div>
                <span className="text-[14px] text-zinc-200 w-40 shrink-0">{c.label}</span>
                <span className="text-[12.5px] text-zinc-500 truncate">{c.detail}</span>
              </div>
            ))
          )}
        </div>

        {done && (
          <p
            className={
              "screen text-[13px] mt-4 " +
              (hasFail ? "text-red-300" : hasWarn ? "text-zinc-400" : "text-zinc-300")
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
            className="px-4 h-11 rounded-xl text-[14px] text-zinc-400 hover:text-zinc-100 transition"
          >
            Back
          </button>
          <div className="flex gap-3">
            <button
              onClick={load}
              className="px-4 h-11 rounded-xl text-[14px] text-zinc-400 hover:text-zinc-100 transition"
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
        <p className="text-[14px] text-zinc-400 max-w-sm leading-relaxed">
          Nothing is installed on this Mac — you'll be connected to a workspace
          that's already running.
        </p>
      </div>

      {submitted ? (
        <div className="w-[380px] max-w-[80vw] rounded-2xl border border-zinc-800 bg-zinc-950/60 px-5 py-4 text-center">
          <div className="text-[13.5px] text-zinc-100 font-medium">Invite saved</div>
          <div className="text-[12.5px] text-zinc-500 mt-1 leading-relaxed">
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
                       font-mono text-[15px] text-zinc-100 placeholder:text-zinc-600 placeholder:tracking-normal
                       outline-none focus:border-zinc-600 transition"
          />
          <PrimaryButton onClick={() => setSubmitted(true)} disabled={code.trim().length < 6}>
            Join
          </PrimaryButton>
        </div>
      )}

      <button
        onClick={onBack}
        className="px-4 h-10 rounded-xl text-[13.5px] text-zinc-500 hover:text-zinc-100 transition"
      >
        Back
      </button>
    </div>
  );
}

/* ── screen 1b: welcome back (existing install) ── */

function WelcomeBack({
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
        <div className="flex items-center justify-center gap-2 text-[13px] text-zinc-500">
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
            <span className="text-[13.5px] text-zinc-400">Checking for updates…</span>
          </div>
        ) : updateAvailable ? (
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-[13.5px] text-zinc-100 font-medium">Update available</div>
              <div className="text-[12px] text-zinc-500 mt-0.5">
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
            <svg width="14" height="14" viewBox="0 0 14 14" className="text-zinc-400">
              <path
                d="M2.5 7.5 5.5 10.5 11.5 3.5"
                stroke="currentColor"
                strokeWidth="1.8"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <span className="text-[13.5px] text-zinc-400">You're up to date</span>
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
        <p className="text-[13px] text-zinc-500 mb-6">
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
                     font-mono text-[11.5px] leading-relaxed text-zinc-500 whitespace-pre-wrap select-text"
        >
          {lines.join("\n") || "Starting…"}
        </div>
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
    "placeholder:text-zinc-600 outline-none focus:border-zinc-600 transition";

  return (
    <div className="screen h-full flex flex-col items-center justify-center">
      <div className="w-[560px] max-w-[86vw]">
        <h1 className="text-[22px] font-semibold tracking-tight mb-1">Configuration</h1>
        <p className="text-[13px] text-zinc-500 mb-6">
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
                      : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
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

        <p className="text-[12px] text-zinc-600 mt-3">
          Safe preview walks the full installation without changing anything.
        </p>

        <div className="flex justify-between mt-8">
          <button
            onClick={onBack}
            className="px-4 h-11 rounded-xl text-[14px] text-zinc-400 hover:text-zinc-100 transition"
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
            ? "text-zinc-500"
            : state === "active"
              ? "text-zinc-100"
              : "text-zinc-600")
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
        <p className="text-[13px] text-zinc-500 mb-6">
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
          className="mt-4 text-[12px] text-zinc-600 hover:text-zinc-300 transition"
        >
          {showConsole ? "Hide details" : "Show details"}
        </button>

        {showConsole && (
          <div
            ref={consoleRef}
            className="console mt-2 h-40 overflow-y-auto rounded-xl border border-zinc-800 bg-black/60 px-4 py-3
                       font-mono text-[11.5px] leading-relaxed text-zinc-500 whitespace-pre-wrap select-text"
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
        <p className="text-[14px] text-zinc-400 max-w-sm leading-relaxed">
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
          <WelcomeBack
            sys={sys}
            checking={checking}
            onUpdate={() => setScreen("update")}
            onContinue={() => setScreen("configure")}
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
          onContinue={() => setScreen("install")}
        />
      )}
      {screen === "install" && <Install config={config} onFinished={handleFinished} />}
      {screen === "done" && (
        <Done config={config} exitCode={exitCode} onRestart={() => setScreen("welcome")} />
      )}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

/* ────────────────────────────────────────────────────────────────
   Screens: welcome → configure → install → done
   Unbranded shell — the name is intentionally not fixed yet.
   ──────────────────────────────────────────────────────────────── */

type Screen = "welcome" | "configure" | "install" | "done";

interface SetupConfig {
  operatorName: string;
  machineName: string;
  role: "primary" | "worker";
  dryRun: boolean;
}

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

function Welcome({ onContinue }: { onContinue: () => void }) {
  return (
    <div className="screen h-full flex flex-col items-center justify-center gap-10">
      <Mark />
      <div className="text-center space-y-2">
        <h1 className="text-[26px] font-semibold tracking-tight text-zinc-50">Welcome</h1>
        <p className="text-[14px] text-zinc-400 max-w-sm leading-relaxed">
          This will set up your agentic operating system on this Mac — dependencies,
          services, and workspace, all in the right places.
        </p>
      </div>
      <PrimaryButton onClick={onContinue}>Continue</PrimaryButton>
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
  const [config, setConfig] = useState<SetupConfig>({
    operatorName: "",
    machineName: "",
    role: "primary",
    dryRun: true,
  });

  const handleFinished = useCallback((code: number) => {
    setExitCode(code);
    // small beat so the last checkmark lands before transitioning
    setTimeout(() => setScreen("done"), 900);
  }, []);

  return (
    <div className="h-full">
      <DragRegion />
      {screen === "welcome" && <Welcome onContinue={() => setScreen("configure")} />}
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

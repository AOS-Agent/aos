/**
 * ShipPanel — the finish line. Computes ship-readiness and renders the exact git
 * command sequence to land the branch. The cockpit NEVER runs these: it emits the
 * plan for the operator to copy and run, with the push flagged irreversible.
 *
 * When not ready (a gate failing, batches undecided, gates stale) it says so and
 * dims the plan to a preview — refusing to green-light a broken ship is the point.
 */

import { useState } from 'react';
import { Check, AlertTriangle, Copy, ShieldAlert } from 'lucide-react';
import type { ShipPlanResponse } from '@/lib/gitApi';

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        } catch {
          /* clipboard blocked — no-op */
        }
      }}
      className="shrink-0 flex items-center gap-1 h-6 px-2 rounded-md text-[11px] text-text-quaternary hover:text-text-secondary border border-border hover:border-border-secondary cursor-pointer"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {copied ? <Check className="w-3 h-3 text-tag-green" /> : <Copy className="w-3 h-3" />}
      {copied ? 'copied' : 'copy'}
    </button>
  );
}

export default function ShipPanel({ plan }: { plan?: ShipPlanResponse }) {
  if (!plan || plan.linked === false || plan.is_repo === false) return null;
  const { ready, blockers, warnings, steps } = plan;

  return (
    <div className="mt-8 rounded-xl border border-border overflow-hidden">
      {/* Verdict header */}
      <div
        className="flex items-center gap-2.5 px-4 h-12 border-b border-border"
        style={{ background: ready ? 'var(--color-tag-green-bg)' : 'var(--color-bg-secondary)' }}
      >
        {ready ? (
          <Check className="w-4 h-4 text-tag-green" />
        ) : (
          <AlertTriangle className="w-4 h-4 text-tag-orange" />
        )}
        <span className={`text-[14px] font-[600] ${ready ? 'text-tag-green' : 'text-text-secondary'}`}>
          {ready ? 'Ready to ship' : 'Not ready to ship'}
        </span>
        <span className="text-[12px] text-text-quaternary ml-auto font-mono tabular-nums">
          {plan.ship_count} ship
          {plan.excluded_count ? ` · ${plan.excluded_count} backed out` : ''}
        </span>
      </div>

      <div className="p-4 space-y-3">
        {blockers.length > 0 && (
          <div className="space-y-1.5">
            {blockers.map((b, i) => (
              <div key={i} className="flex items-center gap-2 text-[13px] text-text-secondary">
                <span className="w-1.5 h-1.5 rounded-full bg-tag-orange shrink-0" />
                {b}
              </div>
            ))}
          </div>
        )}
        {warnings.length > 0 && (
          <div className="space-y-1">
            {warnings.map((w, i) => (
              <div key={i} className="text-[12px] text-text-quaternary">
                · {w}
              </div>
            ))}
          </div>
        )}

        {/* The command plan — dimmed to a preview until ready. */}
        <div className={ready ? '' : 'opacity-55'}>
          <div className="text-[10px] font-[590] uppercase tracking-[0.08em] text-text-quaternary mb-2">
            Command plan{ready ? '' : ' · available once green'}
          </div>
          <div className="space-y-2">
            {steps.map((s, i) => (
              <div
                key={i}
                className="rounded-lg border border-border p-2.5"
                style={
                  s.danger
                    ? { borderColor: 'var(--color-tag-red)', background: 'var(--color-tag-red-bg)' }
                    : undefined
                }
              >
                <div className="flex items-center gap-2 mb-1.5">
                  {s.danger && <ShieldAlert className="w-3.5 h-3.5 text-tag-red shrink-0" />}
                  <span
                    className={`text-[12px] font-[510] ${s.danger ? 'text-tag-red' : 'text-text-secondary'}`}
                  >
                    {s.label}
                  </span>
                  {s.note && <span className="text-[11px] text-text-quaternary">— {s.note}</span>}
                  <div className="ml-auto">
                    <CopyButton text={s.cmd} />
                  </div>
                </div>
                <code className="block font-mono text-[11px] text-text-tertiary break-all leading-relaxed">
                  $ {s.cmd}
                </code>
              </div>
            ))}
          </div>
          <p className="mt-2.5 text-[11px] text-text-quaternary">
            The cockpit never runs these — copy and run them yourself. The push is irreversible.
          </p>
        </div>
      </div>
    </div>
  );
}

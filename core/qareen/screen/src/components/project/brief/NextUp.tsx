/**
 * Next up + Blockers — the two halves of "what now".
 *
 * `next_up` is dependency-aware and capped at three by the compiler, and every
 * item carries a `why`. The why is the point: it is the difference between a
 * list and an answer.
 */

import { timeAgo } from '@/lib/format';
import type { Blocker, NextItem } from '@/hooks/useProjectBrief';
import { Overline } from './chrome';

export function NextUp({ items, onOpenTask }: { items: NextItem[]; onOpenTask: (id: string) => void }) {
  if (items.length === 0) return null;
  return (
    <section>
      <Overline>Next up</Overline>
      <div className="space-y-1.5">
        {items.map((item, i) => (
          <button
            key={item.task_id}
            onClick={() => onOpenTask(item.task_id)}
            className="w-full text-left flex gap-3 bg-bg-secondary hover:bg-bg-tertiary border border-border hover:border-border-secondary rounded-[7px] px-4 py-3 cursor-pointer transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <span className="shrink-0 w-[20px] h-[20px] mt-[1px] rounded-full bg-bg-tertiary text-text-tertiary text-[11px] font-mono flex items-center justify-center tabular-nums">
              {i + 1}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span className="text-[15px] font-[510] text-text truncate">{item.title}</span>
                {item.priority <= 2 && (
                  <span className="shrink-0 text-[10px] font-[590] uppercase tracking-[0.06em] text-tag-red">
                    P{item.priority}
                  </span>
                )}
              </span>
              {item.why && (
                <span className="block font-serif text-[14px] leading-[1.5] text-text-tertiary mt-0.5">
                  {item.why}
                </span>
              )}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

export function Blockers({ blockers, onOpenTask }: { blockers: Blocker[]; onOpenTask: (id: string) => void }) {
  if (blockers.length === 0) return null;
  return (
    <section>
      <Overline count={blockers.length}>Blockers</Overline>
      <div className="space-y-1.5">
        {blockers.map(b => (
          <button
            key={b.task_id}
            onClick={() => onOpenTask(b.task_id)}
            className="w-full text-left flex gap-3 bg-bg-secondary hover:bg-bg-tertiary border border-border hover:border-border-secondary rounded-[7px] px-4 py-3 cursor-pointer transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <span className="shrink-0 w-[6px] h-[6px] rounded-full bg-tag-red mt-[7px]" />
            <span className="min-w-0 flex-1">
              <span className="block text-[15px] font-[510] text-text truncate">{b.title}</span>
              <span className="block font-serif text-[14px] leading-[1.5] text-text-tertiary mt-0.5">
                {b.blocked_on}
                {b.since && <span className="text-text-quaternary"> · stuck since {timeAgo(b.since)}</span>}
              </span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

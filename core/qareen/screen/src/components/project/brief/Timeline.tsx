/**
 * Recent activity — the merged timeline, with the actor named.
 *
 * The contract is emphatic that no state change is anonymous: an agent that
 * completes a task signs off, a hand edit is attributed to the operator, and an
 * unresolved actor renders as "Someone" rather than a guess. The compiler
 * already writes each line in plain English, so when its text opens with the
 * actor's name the chip is suppressed instead of repeating it.
 */

import { isToday, isYesterday, parseISO } from 'date-fns';
import { formatDate, formatTime } from '@/lib/format';
import type { BriefEvent } from '@/hooks/useProjectBrief';
import { actorLabel, eventTone, textNamesActor } from './briefStyle';
import { Overline } from './chrome';

function dayLabel(iso: string): string {
  try {
    const d = parseISO(iso);
    if (isToday(d)) return 'Today';
    if (isYesterday(d)) return 'Yesterday';
    return formatDate(iso, 'EEEE, MMM d');
  } catch {
    return 'Earlier';
  }
}

export function Timeline({ events }: { events: BriefEvent[] }) {
  if (events.length === 0) return null;

  // Newest first, then bucketed by day so the shape of the week is readable.
  const ordered = [...events].sort((a, b) => (a.at < b.at ? 1 : -1));
  const days: { label: string; items: BriefEvent[] }[] = [];
  for (const e of ordered) {
    const label = dayLabel(e.at);
    const last = days[days.length - 1];
    if (last && last.label === label) last.items.push(e);
    else days.push({ label, items: [e] });
  }

  return (
    <section className="mb-10">
      <Overline>Recent activity</Overline>

      <div className="space-y-4">
        {days.map(day => (
          <div key={day.label}>
            <p className="text-[11px] font-[510] text-text-quaternary mb-1 px-1">{day.label}</p>
            <div className="relative pl-[7px]">
              <span className="absolute left-[7px] top-2 bottom-2 w-px bg-border" />
              {day.items.map((e, i) => {
                const tone = eventTone(e.kind);
                const who = actorLabel(e.actor);
                // Commit text always opens with the git author's name, and the
                // compiler deliberately leaves that actor unresolved (an author
                // name is a name, not a verified role). Showing the chip too
                // produced "Someone <author> committed ..." — a contradiction
                // in one line. The sentence already says who; drop the chip.
                const showWho = e.kind !== 'commit' && !textNamesActor(e.text, who);
                return (
                  <div key={`${e.at}-${i}`} className="relative flex items-baseline gap-3 py-1.5 pl-4">
                    <span className={`absolute left-[-3px] top-[10px] w-[7px] h-[7px] rounded-full ring-2 ring-bg ${tone.dot}`} />
                    <span className="min-w-0 flex-1 text-[14px] leading-[1.5] text-text-secondary">
                      {showWho && <span className="text-text font-[510]">{who} </span>}
                      {e.text}
                    </span>
                    <span className="shrink-0 text-[11px] font-mono text-text-quaternary tabular-nums">
                      {formatTime(e.at)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

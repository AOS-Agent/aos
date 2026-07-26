/**
 * Conflicts — how the operator learns their plan has a duplicate spine, or that
 * a doc disagrees with the tracker.
 *
 * Deliberately NOT styled as an error log. Each conflict is a short, actionable
 * sentence in the qareen's voice with a severity edge and the records involved.
 * The compiler reports; resolution is always the operator's call.
 */

import type { Conflict } from '@/hooks/useProjectBrief';
import { conflictLabel, conflictTone, isTaskRef, baseName } from './briefStyle';
import { KindChip, Overline, RefChip } from './chrome';

export function Conflicts({
  conflicts,
  onOpenTask,
}: {
  conflicts: Conflict[];
  onOpenTask: (taskId: string) => void;
}) {
  if (conflicts.length === 0) return null;

  // Errors first — a contradiction outranks a nudge.
  const ordered = [...conflicts].sort(
    (a, b) => (b.severity === 'error' ? 1 : 0) - (a.severity === 'error' ? 1 : 0),
  );

  return (
    <section className="mb-10">
      <Overline count={conflicts.length}>Conflicts</Overline>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2.5">
        {ordered.map((c, i) => {
          const tone = conflictTone(c.severity);
          return (
            <div
              key={`${c.kind}-${i}`}
              className="relative bg-bg-secondary rounded-[7px] pl-4 pr-4 py-3.5 border border-border overflow-hidden"
            >
              <span className={`absolute left-0 top-0 bottom-0 w-[2px] ${tone.dot}`} />

              <div className="flex items-center gap-2 mb-1.5">
                <KindChip label={conflictLabel(c.kind)} tone={tone} />
              </div>

              <p className="font-serif text-[15px] leading-[1.55] text-text-secondary">{c.message}</p>

              {c.refs.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 mt-2.5">
                  {c.refs.map(ref => (
                    <RefChip
                      key={ref}
                      label={isTaskRef(ref) ? ref : baseName(ref)}
                      title={ref}
                      onClick={isTaskRef(ref) ? () => onOpenTask(ref) : undefined}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

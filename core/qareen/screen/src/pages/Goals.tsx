/**
 * Goals — the Work 'Goals' tab, now a strategy room.
 *
 * Presents the operator's goals: each goal (area) with rolled-up, AUTHORITATIVE
 * progress (summed from its projects' task_count / done_count — never the capped
 * tasks[] array), its area color, its linked projects as drill-in cards that open
 * ProjectDetail, and the INITIATIVES that map to this area as drill-in cards that
 * open InitiativeDetail. Initiatives mapping to no shown goal collect under an
 * "Other initiatives" group so nothing is hidden.
 */

import { useWork, type Goal } from '@/hooks/useWork';
import { useInitiatives, type InitiativeSummary } from '@/hooks/useInitiatives';
import { areaTone, initiativeGoalId, type AreaTone } from '@/lib/areaStyle';

// Status → tag color identity (sentence case, lowercase from frontmatter).
const STATUS_TONE: Record<string, { text: string; bg: string }> = {
  executing: { text: 'text-tag-green', bg: 'bg-tag-green-bg' },
  active: { text: 'text-tag-green', bg: 'bg-tag-green-bg' },
  shaping: { text: 'text-tag-purple', bg: 'bg-tag-purple-bg' },
  planning: { text: 'text-tag-blue', bg: 'bg-tag-blue-bg' },
  review: { text: 'text-tag-yellow', bg: 'bg-tag-yellow-bg' },
  done: { text: 'text-tag-gray', bg: 'bg-tag-gray-bg' },
};

function InitiativeCard({
  init,
  tone,
  onOpen,
}: {
  init: InitiativeSummary;
  tone: AreaTone;
  onOpen?: (slug: string) => void;
}) {
  const status = (init.status ?? '').toLowerCase();
  const st = STATUS_TONE[status] ?? { text: 'text-tag-gray', bg: 'bg-tag-gray-bg' };
  return (
    <button
      onClick={() => onOpen?.(init.slug)}
      className="px-3 py-2 rounded-lg bg-bg-secondary border border-border min-w-[180px] text-left hover:bg-bg-tertiary transition-colors duration-75 cursor-pointer"
    >
      <div className="flex items-center gap-2">
        <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${tone.dot}`} />
        <span className="text-[14px] font-[510] text-text-secondary truncate flex-1">{init.title}</span>
        {init.status && (
          <span className={`inline-flex items-center px-1.5 h-[18px] rounded-xs text-[10px] font-medium leading-none shrink-0 ${st.text} ${st.bg}`}>
            {status}
          </span>
        )}
      </div>
    </button>
  );
}

export default function GoalsPage({
  onProjectClick,
  onOpenInitiative,
}: {
  onProjectClick?: (projectId: string) => void;
  onOpenInitiative?: (slug: string) => void;
} = {}) {
  const { data, isLoading } = useWork();
  const { data: initData } = useInitiatives();
  const goals = (data?.goals ?? []) as Goal[];
  const projects = data?.projects ?? [];
  const initiatives = initData?.initiatives ?? [];

  const rows = goals.map((goal, i) => {
    const tone = areaTone(goal.id, i);
    const goalProjects = projects.filter(p => p.goal === goal.id);
    const total = goalProjects.reduce((s, p) => s + (p.task_count ?? 0), 0);   // rolled-up, authoritative
    const done = goalProjects.reduce((s, p) => s + (p.done_count ?? 0), 0);
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    const goalInitiatives = initiatives.filter(it => initiativeGoalId(it.project) === goal.id);
    return { goal, tone, goalProjects, total, done, pct, goalInitiatives };
  });

  // Initiatives that map to no SHOWN goal — surfaced so nothing is hidden.
  const shownGoalIds = new Set(goals.map(g => g.id));
  const otherInitiatives = initiatives.filter(it => {
    const gid = initiativeGoalId(it.project);
    return !gid || !shownGoalIds.has(gid);
  });

  if (isLoading) return <div className="flex items-center justify-center h-full"><p className="text-text-quaternary">Loading…</p></div>;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[880px] mx-auto px-6 py-8">
        {rows.length === 0 && otherInitiatives.length === 0 ? (
          <div className="py-16 text-center">
            <p className="text-[17px] text-text-quaternary opacity-50">No goals defined yet.</p>
            <p className="text-[13px] text-text-quaternary opacity-30 mt-1">Areas and their key results will appear here once you set them.</p>
          </div>
        ) : (
          <>
            {rows.map(({ goal, tone, goalProjects, total, done, pct, goalInitiatives }) => (
              <div key={goal.id} className="mb-9">
                {/* Goal header */}
                <div className="flex items-center gap-2.5 mb-2">
                  <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${tone.dot}`} />
                  <span className="text-[17px] font-[600] text-text truncate flex-1">{goal.title}</span>
                  <span className="text-[12px] font-mono text-text-quaternary shrink-0">{done}/{total}</span>
                  <span className={`text-[13px] font-mono font-[510] shrink-0 ${tone.text}`}>{pct}%</span>
                </div>

                {/* Rolled-up progress */}
                <div className="h-1.5 bg-bg-tertiary rounded-full overflow-hidden mb-4">
                  <div className={`h-full rounded-full ${tone.dot}`} style={{ width: `${pct}%` }} />
                </div>

                {/* Key results */}
                {goal.key_results && goal.key_results.length > 0 && (
                  <div className="pl-1 mb-4 space-y-1.5">
                    {goal.key_results.map((kr, ki) => {
                      const krPct = kr.target > 0 ? Math.min(100, Math.round((kr.progress / kr.target) * 100)) : 0;
                      return (
                        <div key={ki} className="flex items-center gap-2 text-[13px]">
                          <span className="text-text-tertiary truncate flex-1">{kr.title}</span>
                          <span className="text-text-quaternary font-mono shrink-0">{kr.progress}/{kr.target}</span>
                          <span className={`font-mono shrink-0 ${tone.text}`}>{krPct}%</span>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Linked projects — drill into the same ProjectDetail */}
                {goalProjects.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {goalProjects.map(proj => {
                      const pTotal = proj.task_count ?? 0;
                      const pDone = proj.done_count ?? 0;
                      const pPct = pTotal > 0 ? Math.round((pDone / pTotal) * 100) : 0;
                      return (
                        <button
                          key={proj.id}
                          onClick={() => onProjectClick?.(proj.id)}
                          className="px-3 py-2 rounded-lg bg-bg-secondary border border-border min-w-[150px] text-left hover:bg-bg-tertiary transition-colors duration-75 cursor-pointer"
                        >
                          <div className="flex items-center gap-2 mb-1.5">
                            <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${tone.dot}`} />
                            <span className="text-[14px] font-[510] text-text-secondary truncate flex-1">{proj.title}</span>
                            <span className="text-[11px] font-mono text-text-quaternary shrink-0">{pDone}/{pTotal}</span>
                          </div>
                          <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden">
                            <div className={`h-full rounded-full ${tone.dot}`} style={{ width: `${pPct}%` }} />
                          </div>
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-[13px] text-text-quaternary px-1">No projects linked to this goal yet.</p>
                )}

                {/* Initiatives — the strategy under this area, drill into InitiativeDetail */}
                {goalInitiatives.length > 0 && (
                  <div className="mt-4">
                    <p className="text-[11px] font-[590] uppercase tracking-[0.06em] text-text-tertiary mb-2 px-1">Initiatives</p>
                    <div className="flex flex-wrap gap-2">
                      {goalInitiatives.map(it => (
                        <InitiativeCard key={it.slug} init={it} tone={tone} onOpen={onOpenInitiative} />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}

            {/* Other initiatives — map to no shown area, surfaced so nothing is hidden */}
            {otherInitiatives.length > 0 && (
              <div className="mb-9">
                <div className="flex items-center gap-2.5 mb-3">
                  <span className="w-2.5 h-2.5 rounded-full shrink-0 bg-tag-gray" />
                  <span className="text-[17px] font-[600] text-text truncate flex-1">Other initiatives</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {otherInitiatives.map(it => (
                    <InitiativeCard key={it.slug} init={it} tone={areaTone(initiativeGoalId(it.project), 0)} onOpen={onOpenInitiative} />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

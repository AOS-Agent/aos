/**
 * InitiativeDetail — full-canvas initiative view.
 *
 * Mirrors ProjectDetail: opened by drilling into an initiative card from the
 * Goals tab, it renders as a real page that replaces the list while the active
 * tab stays mounted behind it — Back returns to exactly where you came from.
 *
 * Shows the initiative's area color, status + appetite, its rendered doc body on
 * a reading surface (EB Garamond), and — when the initiative's `project` maps to
 * a real work project — a small Execution card that drills into ProjectDetail.
 */

import { useState, useEffect, useCallback } from 'react';
import { ArrowLeft } from 'lucide-react';
import { useInitiative } from '@/hooks/useInitiatives';
import { useWork, type Goal } from '@/hooks/useWork';
import { areaTone, initiativeGoalId } from '@/lib/areaStyle';
import { MarkdownRenderer } from '@/components/primitives/MarkdownRenderer';

// Status → tag color identity (sentence case, lowercase from frontmatter).
const STATUS_TONE: Record<string, { text: string; bg: string }> = {
  executing: { text: 'text-tag-green', bg: 'bg-tag-green-bg' },
  active: { text: 'text-tag-green', bg: 'bg-tag-green-bg' },
  shaping: { text: 'text-tag-purple', bg: 'bg-tag-purple-bg' },
  planning: { text: 'text-tag-blue', bg: 'bg-tag-blue-bg' },
  review: { text: 'text-tag-yellow', bg: 'bg-tag-yellow-bg' },
  done: { text: 'text-tag-gray', bg: 'bg-tag-gray-bg' },
};

// An initiative's area maps to ONE primary work project we can show execution for.
function workProjectIdFor(projectSlug: string | null | undefined): string | null {
  const goal = initiativeGoalId(projectSlug);
  if (goal === 'aos-infrastructure') return 'aos';
  if (goal === 'dod-launch') return 'dod';
  return null;
}

export default function InitiativeDetail({
  slug,
  onBack,
  onOpenProject,
}: {
  slug: string;
  onBack: () => void;
  onOpenProject?: (projectId: string) => void;
}) {
  const { data: init, isLoading } = useInitiative(slug);
  const { data: work } = useWork();

  const goals = (work?.goals ?? []) as Goal[];
  const projects = work?.projects ?? [];

  const goalId = initiativeGoalId(init?.project);
  const tone = areaTone(goalId, 0);
  const goal = goals.find(g => g.id === goalId);

  const status = (init?.status ?? '').toLowerCase();
  const statusTone = STATUS_TONE[status] ?? { text: 'text-tag-gray', bg: 'bg-tag-gray-bg' };

  // Execution card — only when the initiative's area maps to a real work project.
  const execId = workProjectIdFor(init?.project);
  const execProject = execId ? projects.find(p => p.id === execId) : undefined;
  const execTotal = execProject?.task_count ?? 0;
  const execDone = execProject?.done_count ?? 0;
  const execActive = execProject?.active_count ?? 0;
  const execPct = execTotal > 0 ? Math.round((execDone / execTotal) * 100) : 0;

  // Close animation (DESIGN rule 7) — owned internally so Work's onBack stays a one-liner.
  const [closing, setClosing] = useState(false);
  const handleClose = useCallback(() => setClosing(true), []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') handleClose(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [handleClose]);

  const backLink = (
    <button
      onClick={handleClose}
      className="flex items-center gap-1.5 text-[13px] text-text-quaternary hover:text-text-tertiary cursor-pointer"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <ArrowLeft className="w-3 h-3" />
      Goals
    </button>
  );

  const keyframes = <style>{`@keyframes initIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}@keyframes initOut{from{opacity:1;transform:none}to{opacity:0;transform:translateY(6px)}}`}</style>;
  const animClass = closing ? 'animate-[initOut_160ms_ease-in]' : 'animate-[initIn_180ms_ease-out]';

  if (isLoading) {
    return (
      <div className={`h-full overflow-y-auto ${animClass}`} onAnimationEnd={() => { if (closing) onBack(); }}>
        <div className="flex items-center justify-center h-full"><p className="text-text-quaternary">Loading…</p></div>
        {keyframes}
      </div>
    );
  }

  if (!init) {
    return (
      <div className={`h-full overflow-y-auto ${animClass}`} onAnimationEnd={() => { if (closing) onBack(); }}>
        <div className="max-w-[880px] mx-auto px-6 py-8">
          {backLink}
          <p className="text-[15px] text-text-quaternary mt-6">Initiative not found.</p>
        </div>
        {keyframes}
      </div>
    );
  }

  return (
    <div className={`h-full overflow-y-auto ${animClass}`} onAnimationEnd={() => { if (closing) onBack(); }}>
      <div className="max-w-[880px] mx-auto px-6 py-8">
        {/* Back */}
        {backLink}

        {/* Header — clear "Initiative" eyebrow so you always know what you're viewing */}
        <div className="mt-7 mb-8">
          <div className="flex items-center gap-2 mb-3">
            <span className={`text-[12px] font-[680] uppercase tracking-[0.12em] ${tone.text}`}>Initiative</span>
            <span className="w-1 h-1 rounded-full bg-text-quaternary shrink-0" />
            <span className={`w-2 h-2 rounded-full shrink-0 ${tone.dot}`} />
            <span className="text-[14px] text-text-tertiary truncate">{goal?.title ?? 'No area'}</span>
          </div>
          <h1 className="text-[30px] font-[680] text-text tracking-[-0.02em] leading-[1.15] mb-3.5">{init.title}</h1>
          <div className="flex items-center gap-2.5">
            {init.status && (
              <span className={`inline-flex items-center px-2.5 h-6 rounded-md text-[12px] font-[510] leading-[1.2] ${statusTone.text} ${statusTone.bg}`}>
                {status}
              </span>
            )}
            {init.appetite && (
              <span className="text-[13px] text-text-quaternary">{init.appetite}</span>
            )}
          </div>
        </div>

        {/* Execution — links to the work project that delivers this initiative */}
        {execProject && (
          <button
            onClick={() => onOpenProject?.(execProject.id)}
            disabled={!onOpenProject}
            className="w-full text-left mb-8 px-4 py-3 rounded-lg bg-bg-secondary border border-border hover:bg-bg-tertiary transition-colors duration-75 cursor-pointer disabled:cursor-default disabled:hover:bg-bg-secondary"
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[11px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">Execution</span>
              <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${tone.dot}`} />
              <span className="text-[14px] font-[510] text-text-secondary truncate flex-1">{execProject.title}</span>
              <span className="text-[11px] font-mono text-text-quaternary shrink-0">{execDone}/{execTotal}</span>
              <span className={`text-[12px] font-mono font-[510] shrink-0 ${tone.text}`}>{execPct}%</span>
            </div>
            <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden">
              <div className={`h-full rounded-full ${tone.dot}`} style={{ width: `${execPct}%` }} />
            </div>
            <p className="text-[12px] text-text-quaternary mt-2">{execActive} active · {execDone} done</p>
          </button>
        )}

        {/* Doc body — clean base sans (Notion-style), not serif */}
        <MarkdownRenderer content={init.content} className="font-sans" />
      </div>
      {keyframes}
    </div>
  );
}

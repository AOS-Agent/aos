/**
 * WorktreeStrip — a compact, muted chip row of the repo's worktrees.
 *
 * Most repos have one checkout; this stays a single quiet chip rather than a
 * stat dump. When sibling worktrees exist (parallel branches, agent isolation)
 * each gets a chip, with the current checkout marked by an accent dot.
 */

import { GitBranch } from 'lucide-react';
import type { Worktree } from '@/lib/gitApi';

export default function WorktreeStrip({ worktrees }: { worktrees: Worktree[] }) {
  if (!worktrees.length) return null;

  return (
    <div className="flex items-center gap-2 flex-wrap mb-5 -mt-1.5">
      <span className="text-[10px] font-[590] uppercase tracking-[0.08em] text-text-quaternary">
        {worktrees.length === 1 ? 'Worktree' : `${worktrees.length} worktrees`}
      </span>
      {worktrees.map((w) => {
        const label = w.branch
          ? w.branch
          : w.detached
            ? `detached · ${w.head}`
            : w.bare
              ? 'bare'
              : w.head;
        return (
          <span
            key={w.path}
            title={w.path}
            className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full border"
            style={{
              borderColor: w.is_current
                ? 'var(--color-accent-subtle)'
                : 'var(--color-border)',
            }}
          >
            <GitBranch className="w-3 h-3 text-text-quaternary" />
            <span className="font-mono text-[11px] text-text-tertiary">{label}</span>
            {w.is_current && (
              <span
                className="w-1.5 h-1.5 rounded-full bg-accent"
                aria-label="this checkout"
              />
            )}
          </span>
        );
      })}
    </div>
  );
}

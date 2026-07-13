/**
 * GitView — the Git/Ship cockpit container (v1 foundation).
 *
 * Rendered inside ProjectDetail only when the project is linked to a git repo.
 * It fuses the two halves on one surface:
 *   VISUALIZE — BranchGauge header + the bounded unmerged commit list (the cargo
 *               above origin/main's ship line). The SVG lane graph is a deferred
 *               follow-on; v1 ships the list form with batch-color tinting.
 *   GUIDE     — ShipLedger: the 14-batch walkthrough with audit status + operator
 *               decision, persisted to ~/.aos/ship/<project>--<branch>.yaml.
 *
 * The halves are joined by stable SHAs: hovering a batch spotlights its commits
 * in the list (shared selectedBatchId). Graceful states throughout — never a
 * generic "no data".
 */

import { useMemo, useState, type ReactNode } from 'react';
import { GitBranch } from 'lucide-react';
import type { AreaTone } from '@/lib/areaStyle';
import {
  useGitStatus,
  useGitCommits,
  useGitBelow,
  useGitWorktrees,
  useShipPlan,
  useShipReadiness,
  useGitRefresh,
  useGateProgress,
} from '@/hooks/useGit';
import BranchGauge from './BranchGauge';
import CommitGraph from './CommitGraph';
import WorktreeStrip from './WorktreeStrip';
import ShipLedger from './ShipLedger';
import ShipPanel from './ShipPanel';

function Empty({ icon, title, sub }: { icon?: ReactNode; title: string; sub?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      {icon && <div className="mb-3 text-text-quaternary">{icon}</div>}
      <p className="text-[15px] text-text-tertiary">{title}</p>
      {sub && <p className="text-[13px] text-text-quaternary mt-1.5 max-w-[420px]">{sub}</p>}
    </div>
  );
}

export default function GitView({
  projectId,
  path,
}: {
  projectId: string;
  path: string;
  tone?: AreaTone;
}) {
  const status = useGitStatus(projectId, path);
  const [limit, setLimit] = useState(60);
  const commitsQ = useGitCommits(projectId, path, limit);
  const belowQ = useGitBelow(projectId, path, 6);
  const worktreesQ = useGitWorktrees(projectId, path);
  const planQ = useShipPlan(projectId, path);
  const shipQ = useShipReadiness(projectId, path);
  const refresh = useGitRefresh(projectId);
  // Live gate progress streams into the cached plan while the cockpit is open.
  useGateProgress(projectId, true);

  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);

  const plan = planQ.data;
  const commits = commitsQ.data?.commits ?? [];

  // sha → batch (ordinal/id) join, for cross-highlighting the commit list.
  const shaBatch = useMemo(() => {
    const m = new Map<string, { id: string; ordinal: number }>();
    plan?.batches.forEach((b) => b.commits.forEach((s) => m.set(s, { id: b.id, ordinal: b.ordinal })));
    return m;
  }, [plan]);

  const clearedLabel = useMemo(() => {
    if (!plan) return undefined;
    const shipDecided = plan.batches.filter((b) => b.decision === 'ship').length;
    const shipSuggested = plan.batches.filter(
      (b) => b.decision === 'undecided' && b.suggested_decision === 'ship',
    ).length;
    const total = plan.batches.length;
    if (shipDecided > 0) return `${shipDecided} of ${total} batches cleared to ship`;
    if (shipSuggested > 0) return `${shipSuggested} of ${total} batches suggested to ship`;
    return `${total} batches to review`;
  }, [plan]);

  // --- Graceful gate states -------------------------------------------------

  const s = status.data;
  const refreshing =
    status.isFetching ||
    commitsQ.isFetching ||
    planQ.isFetching ||
    belowQ.isFetching ||
    worktreesQ.isFetching;

  if (status.isLoading) {
    return <Empty icon={<GitBranch className="w-6 h-6" />} title="Checking the repo…" />;
  }
  if (status.isError) {
    return (
      <Empty
        icon={<GitBranch className="w-6 h-6" />}
        title="Couldn't read the repo"
        sub="The git backend didn't respond. Try Refresh — git on the external SSD can be slow."
      />
    );
  }
  if (s && s.linked === false) {
    return (
      <Empty
        icon={<GitBranch className="w-6 h-6" />}
        title="This project isn't linked to a repo yet"
        sub="Link a repository path to open the cockpit."
      />
    );
  }
  if (s && s.is_repo === false) {
    return (
      <Empty
        icon={<GitBranch className="w-6 h-6" />}
        title="Linked path isn't a git repo"
        sub={s.reason ? `(${s.reason})` : path}
      />
    );
  }
  if (s && s.error === 'git_timeout') {
    return (
      <Empty
        icon={<GitBranch className="w-6 h-6" />}
        title="git timed out"
        sub="The repo is large and on a slow disk. Hit Refresh to retry."
      />
    );
  }

  return (
    <div>
      <BranchGauge
        status={s}
        loading={status.isLoading}
        clearedLabel={clearedLabel}
        onRefresh={refresh}
        refreshing={refreshing}
      />

      <WorktreeStrip worktrees={worktreesQ.data?.worktrees ?? []} />

      {/* Two-pane body — commit list (left) + ship walkthrough (right) */}
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-6">
        {/* Zone 2A — the commit graph (the cargo above the ship line). */}
        <div className="order-2 lg:order-1">
          {commitsQ.isLoading ? (
            <div className="space-y-1.5">
              {[0, 1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="h-11 rounded-lg bg-bg-secondary animate-pulse" style={{ opacity: 0.6 }} />
              ))}
            </div>
          ) : (
            <CommitGraph
              commits={commits}
              total={commitsQ.data?.total ?? commits.length}
              truncated={!!commitsQ.data?.truncated}
              ahead={s?.ahead}
              base={s?.base ?? commitsQ.data?.base ?? undefined}
              shaBatch={shaBatch}
              selectedBatchId={selectedBatchId}
              onSelectBatch={setSelectedBatchId}
              onLoadOlder={() => setLimit((l) => Math.min(200, l + 60))}
              loadingOlder={commitsQ.isFetching && limit > 60}
              below={belowQ.data?.commits}
            />
          )}
        </div>

        {/* Zone 2B — ship walkthrough */}
        <div className="order-1 lg:order-2">
          {planQ.isLoading ? (
            <div className="space-y-2">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="h-14 rounded-xl bg-bg-secondary animate-pulse" style={{ opacity: 0.6 }} />
              ))}
            </div>
          ) : plan ? (
            <ShipLedger
              plan={plan}
              commits={commits}
              projectId={projectId}
              selectedBatchId={selectedBatchId}
              onSelectBatch={setSelectedBatchId}
            />
          ) : (
            <Empty title="No ship plan yet" sub="Seed the plan from the triage spec to begin." />
          )}
        </div>
      </div>

      {/* The finish line — readiness verdict + the approval-gated command plan. */}
      <ShipPanel plan={shipQ.data} />
    </div>
  );
}

/**
 * CommitGraph — the VISUALIZE half, upgraded from a flat list to an SVG lane graph.
 *
 * One continuous SVG gutter renders behind the commit rows so edges flow across
 * rows: straight verticals for council-substrate's near-linear history, diagonals
 * where a branch merges in. Nodes are tinted to their ship-batch color (the same
 * thread the ShipLedger uses), HEAD wears an accent ring, merge commits a hollow
 * ring. The SIGNATURE is the dashed origin/main ship line at the bottom: the
 * unmerged cargo glows above it; merged context (Part 3) dims below.
 *
 * Pure presentation — lane math lives in lib/lanes.ts, data in GitView.
 */

import { useMemo } from 'react';
import { GitMerge } from 'lucide-react';
import type { GitCommit } from '@/lib/gitApi';
import { batchToneKey } from '@/lib/gitApi';
import { assignLanes, laneDivergence } from '@/lib/lanes';

// Geometry — ROW_H matches the old list row (h-11) so the switch is seamless.
const ROW_H = 44;
const LANE_GAP = 16;
const PAD_L = 14;
const PAD_R = 14;
const NODE_R = 5;
const HEAD_RING_R = 8.5;

const laneX = (col: number) => PAD_L + col * LANE_GAP;
const rowY = (i: number) => i * ROW_H + ROW_H / 2;

/** A batch ordinal → its themed tag color var; neutral when a commit has no batch. */
function nodeColor(ordinal?: number): string {
  if (ordinal == null) return 'var(--color-text-quaternary)';
  return `var(--color-tag-${batchToneKey(ordinal)})`;
}

/** A commit is HEAD only if a ref IS "HEAD" or "HEAD -> branch" — not a mere
 *  substring (which would also match origin/HEAD or a branch like feature/AHEAD). */
const isHeadRefs = (refs: string[]) =>
  refs.some((r) => r === 'HEAD' || r.startsWith('HEAD ->'));

/** Edge from a child node down to a parent: bend OUT of the child's column in the
 *  first row, then run straight down the parent's column. Bending at the bottom
 *  instead would paint the long vertical over every mainline node in between. */
function edgePath(x1: number, y1: number, x2: number, y2: number, rowH: number): string {
  if (x1 === x2) return `M ${x1} ${y1} L ${x2} ${y2}`;
  return `M ${x1} ${y1} C ${x1} ${y1 + rowH / 2} ${x2} ${y1 + rowH / 2} ${x2} ${y1 + rowH} L ${x2} ${y2}`;
}

interface CommitGraphProps {
  commits: GitCommit[];
  total: number;
  truncated: boolean;
  ahead?: number;
  base?: string | null;
  /** sha → its batch (for tinting + cross-highlight with the ledger). */
  shaBatch: Map<string, { id: string; ordinal: number }>;
  selectedBatchId: string | null;
  onSelectBatch: (id: string | null) => void;
  /** Reveal more of the unmerged set (older commits, nearer the ship line). */
  onLoadOlder?: () => void;
  loadingOlder?: boolean;
  /** Bounded merged commits beneath origin/main — dimmed below the ship line. */
  below?: GitCommit[];
}

export default function CommitGraph({
  commits,
  total,
  truncated,
  ahead,
  base,
  shaBatch,
  selectedBatchId,
  onSelectBatch,
  onLoadOlder,
  loadingOlder,
  below,
}: CommitGraphProps) {
  const layout = useMemo(() => assignLanes(commits), [commits]);
  const { rows, laneCount, shaToRow } = layout;

  const gutterW = PAD_L + (laneCount - 1) * LANE_GAP + PAD_R;
  const svgH = rows.length * ROW_H;
  const baseShort = base?.replace(/^origin\//, '') ?? 'main';

  const divergence = laneDivergence(commits.length, ahead, truncated);

  // Per-row opacity for the spotlight/dim cross-highlight (shared selectedBatchId).
  const rowOpacity = (sha: string): number => {
    if (selectedBatchId === null) return 1;
    const b = shaBatch.get(sha);
    return b && b.id === selectedBatchId ? 1 : 0.32;
  };

  if (commits.length === 0) {
    return (
      <p className="text-[13px] text-text-quaternary py-6 text-center">
        Nothing ahead of {baseShort}.
      </p>
    );
  }

  return (
    <div>
      {/* Header — sentence-case label + live/total count (mono). */}
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[10px] font-[590] uppercase tracking-[0.08em] text-text-tertiary">
          Commit graph
        </h3>
        <span className="text-[12px] font-mono text-text-quaternary tabular-nums">
          {commits.length}
          {truncated ? ` / ${total}` : ''}
        </span>
      </div>

      {divergence && (
        <div className="mb-2 px-3 py-2 rounded-lg text-[12px] text-tag-orange bg-tag-orange-bg border border-border">
          Showing {divergence.rendered} of {divergence.ahead} commits git reports
          ahead — the base may have moved. Hit Refresh.
        </div>
      )}

      {/* Graph body — absolute SVG gutter under the commit rows. */}
      <div
        className="relative rounded-xl border border-border overflow-hidden"
        style={{
          // Faint accent wash = the unmerged cargo glowing above the ship line.
          background:
            'linear-gradient(to bottom, var(--color-accent-subtle), transparent 64%)',
        }}
      >
        <svg
          className="absolute top-0 left-0 pointer-events-none"
          width={gutterW}
          height={svgH}
          aria-hidden="true"
        >
          {/* Edges first, so nodes sit on top. */}
          {rows.map((r) =>
            r.edges.map((e) => {
              const x1 = laneX(r.col);
              const y1 = rowY(shaToRow.get(r.commit.sha) ?? 0);
              const x2 = laneX(e.toCol);
              // Below-window parent → descend straight into the ship line.
              const y2 = e.toRow != null ? rowY(e.toRow) : svgH;
              const tone = nodeColor(shaBatch.get(r.commit.sha)?.ordinal);
              const d = edgePath(x1, y1, x2, y2, ROW_H);
              return (
                <path
                  key={`${r.commit.sha}-${e.toSha}`}
                  d={d}
                  fill="none"
                  stroke={e.toRow != null ? tone : 'var(--color-border-tertiary)'}
                  strokeWidth={1.5}
                  strokeLinecap="round"
                  opacity={
                    (selectedBatchId === null ? 0.85 : rowOpacity(r.commit.sha)) *
                    (e.toRow != null ? 1 : 0.5)
                  }
                />
              );
            }),
          )}

          {/* Nodes. */}
          {rows.map((r, i) => {
            const x = laneX(r.col);
            const y = rowY(i);
            const tone = nodeColor(shaBatch.get(r.commit.sha)?.ordinal);
            const isMerge = r.commit.parents.length > 1;
            const isHead = isHeadRefs(r.commit.refs);
            const op = rowOpacity(r.commit.sha);
            return (
              <g key={r.commit.sha} opacity={op}>
                {isHead && (
                  <circle
                    cx={x}
                    cy={y}
                    r={HEAD_RING_R}
                    fill="none"
                    stroke="var(--color-accent)"
                    strokeWidth={1.5}
                  />
                )}
                <circle
                  cx={x}
                  cy={y}
                  r={NODE_R}
                  fill={isMerge ? 'var(--color-bg)' : tone}
                  stroke={tone}
                  strokeWidth={isMerge ? 2 : 0}
                />
              </g>
            );
          })}
        </svg>

        {/* Commit rows — text offset past the gutter; whole row drives the highlight. */}
        <div className="relative">
          {rows.map((r) => {
            const c = r.commit;
            const b = shaBatch.get(c.sha);
            const isHead = isHeadRefs(c.refs);
            const isMerge = c.parents.length > 1;
            const spotlight = !!b && b.id === selectedBatchId;
            return (
              <div
                key={c.sha}
                className="flex items-center gap-2.5 h-11 pr-3 border-b border-border last:border-b-0 cursor-default"
                style={{
                  paddingLeft: gutterW,
                  transitionProperty: 'opacity, background',
                  transitionDuration: 'var(--duration-fast)',
                  opacity: rowOpacity(c.sha),
                  background: spotlight ? 'var(--color-bg-secondary)' : undefined,
                }}
                onMouseEnter={() => b && onSelectBatch(b.id)}
                onMouseLeave={() => onSelectBatch(null)}
              >
                <span className="shrink-0 font-mono text-[11px] text-text-quaternary tabular-nums">
                  {c.short}
                </span>
                {isHead && (
                  <span className="shrink-0 font-mono text-[10px] px-1.5 h-4 inline-flex items-center rounded text-accent border border-accent/40">
                    HEAD
                  </span>
                )}
                {isMerge && (
                  <GitMerge className="shrink-0 w-3 h-3 text-text-quaternary" aria-label="merge commit" />
                )}
                <span className="flex-1 min-w-0 text-[13px] text-text-secondary truncate">
                  {c.subject}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Load older — reveals more of the unmerged set (older, nearer the line).
          Hidden at the 200 hard cap: beyond it the button would no-op forever. */}
      {truncated && onLoadOlder && commits.length < 200 && (
        <button
          onClick={onLoadOlder}
          disabled={loadingOlder}
          className="mt-2 w-full h-9 rounded-lg text-[12px] text-text-tertiary hover:text-text-secondary border border-dashed border-border hover:border-border-secondary cursor-pointer disabled:opacity-50"
          style={{ transitionDuration: 'var(--duration-fast)' }}
        >
          {loadingOlder
            ? 'Loading…'
            : `Load older — ${Math.min(total - commits.length, 200 - commits.length)} more`}
        </button>
      )}
      {truncated && commits.length >= 200 && (
        <p className="mt-2 text-[11px] text-text-quaternary text-center">
          Showing the most recent 200 of {total} — older commits sit below the line.
        </p>
      )}

      {/* The ship line — origin/main boundary the cargo sits above. */}
      <div className="flex items-center gap-2 mt-3 px-1">
        <span className="font-mono text-[11px] text-text-secondary">
          {base ?? 'origin/main'}
        </span>
        <div
          className="flex-1 border-t border-dashed"
          style={{ borderColor: 'var(--color-accent)', opacity: 0.6 }}
        />
        <span className="text-[10px] font-[590] uppercase tracking-[0.08em] text-text-tertiary">
          ship line
        </span>
      </div>

      {/* Below the line — the merged ground the branch will land on (dimmed). */}
      {below && below.length > 0 && (
        <BelowLine commits={below} base={base} />
      )}
    </div>
  );
}

/**
 * BelowLine — a compact, dimmed mini-graph of the merged commits beneath the
 * ship line. Same lane math, neutral tones, smaller rows: it shows the ground
 * the branch lands on (and renders real merges into main as a fork).
 */
function BelowLine({ commits, base }: { commits: GitCommit[]; base?: string | null }) {
  const { rows, laneCount, shaToRow } = useMemo(() => assignLanes(commits), [commits]);
  const BELOW_ROW_H = 34;
  const gutterW = PAD_L + (laneCount - 1) * LANE_GAP + PAD_R;
  const svgH = rows.length * BELOW_ROW_H;
  const cy = (i: number) => i * BELOW_ROW_H + BELOW_ROW_H / 2;
  const baseShort = base?.replace(/^origin\//, '') ?? 'main';

  return (
    <div className="mt-2.5 opacity-[0.45]">
      <div className="text-[10px] font-[590] uppercase tracking-[0.08em] text-text-quaternary mb-1.5 px-1">
        Merged into {baseShort}
      </div>
      <div className="relative">
        <svg className="absolute top-0 left-0 pointer-events-none" width={gutterW} height={svgH} aria-hidden="true">
          {rows.map((r) =>
            r.edges.map((e) => {
              const x1 = laneX(r.col);
              const y1 = cy(shaToRow.get(r.commit.sha) ?? 0);
              const x2 = laneX(e.toCol);
              const y2 = e.toRow != null ? cy(e.toRow) : svgH;
              const d = edgePath(x1, y1, x2, y2, BELOW_ROW_H);
              return (
                <path
                  key={`${r.commit.sha}-${e.toSha}`}
                  d={d}
                  fill="none"
                  stroke="var(--color-border-tertiary)"
                  strokeWidth={1.5}
                  strokeLinecap="round"
                />
              );
            }),
          )}
          {rows.map((r, i) => {
            const isMerge = r.commit.parents.length > 1;
            return (
              <circle
                key={r.commit.sha}
                cx={laneX(r.col)}
                cy={cy(i)}
                r={4}
                fill={isMerge ? 'var(--color-bg)' : 'var(--color-text-quaternary)'}
                stroke="var(--color-text-quaternary)"
                strokeWidth={isMerge ? 1.5 : 0}
              />
            );
          })}
        </svg>
        <div className="relative">
          {rows.map((r) => {
            const c = r.commit;
            const isMerge = c.parents.length > 1;
            return (
              <div
                key={c.sha}
                className="flex items-center gap-2.5 cursor-default"
                style={{ paddingLeft: gutterW, height: 34 }}
              >
                <span className="shrink-0 font-mono text-[11px] text-text-quaternary tabular-nums">
                  {c.short}
                </span>
                {isMerge && (
                  <GitMerge className="shrink-0 w-3 h-3 text-text-quaternary" aria-label="merge commit" />
                )}
                <span className="flex-1 min-w-0 text-[12px] text-text-tertiary truncate">
                  {c.subject}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

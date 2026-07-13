// ---------------------------------------------------------------------------
// lanes.ts — greedy lane assignment over the unmerged commit DAG.
//
// Pure frontend, no backend pass: /commits already returns {sha, parents[]} for
// the bounded base..HEAD window (newest→oldest, date-order). We assign every
// commit a column ("lane") so CommitGraph can draw straight verticals for the
// common linear case and diagonals where branches fork/merge.
//
// The algorithm is the classic gitk top-down sweep: a list of OPEN lanes, each
// holding the sha it is next heading toward. A commit takes the lane reserved
// for its sha (or a fresh one if it's a tip), hands its first parent down the
// same lane, and routes extra (merge) parents into sibling lanes. Near-linear
// history — every commit single-parent — collapses to a single lane 0, which is
// exactly what council-substrate looks like today.
// ---------------------------------------------------------------------------

import type { GitCommit } from './gitApi';

/** One commit placed on the graph, with the edges descending to its parents. */
export interface LaneRow {
  commit: GitCommit;
  /** Column the commit's node sits in. */
  col: number;
  /** Edges from this node down to each parent. */
  edges: LaneEdge[];
  /** True when ≥1 parent falls below the rendered window (descends to the ship line). */
  hasBelowLineParent: boolean;
}

export interface LaneEdge {
  /** Column of the parent's node (where the line lands). */
  toCol: number;
  /** Row index of the parent within the window, or null when below the window. */
  toRow: number | null;
  /** Parent sha — stable id for the path key. */
  toSha: string;
}

export interface LaneLayout {
  rows: LaneRow[];
  /** Total columns in use — drives the SVG gutter width. */
  laneCount: number;
  /** sha → row index, for joining edges to their parent rows. */
  shaToRow: Map<string, number>;
}

/**
 * Assign lanes to `commits` (newest→oldest, as /commits returns them).
 *
 * Returns a column per commit plus resolved parent edges. Parents outside the
 * window (older than the last rendered commit) are flagged via `toRow: null` so
 * the renderer can fade them into the ship line instead of dangling.
 */
export function assignLanes(commits: GitCommit[]): LaneLayout {
  const shaToRow = new Map<string, number>();
  commits.forEach((c, i) => shaToRow.set(c.sha, i));

  // lanes[col] = sha the line in that column is next heading toward, or null=free.
  const lanes: (string | null)[] = [];
  const rows: LaneRow[] = [];
  let laneCount = 0;

  const takeFreeLane = (): number => {
    const free = lanes.indexOf(null);
    if (free !== -1) return free;
    lanes.push(null);
    return lanes.length - 1;
  };

  for (let i = 0; i < commits.length; i++) {
    const c = commits[i];

    // 1. This commit's column: the lane reserved for its sha, else a fresh tip lane.
    let col = lanes.indexOf(c.sha);
    if (col === -1) col = takeFreeLane();

    // 2. Clear EVERY lane waiting for this sha (multiple children converge here);
    //    the merged-in sibling lanes free up below this row.
    for (let k = 0; k < lanes.length; k++) {
      if (lanes[k] === c.sha) lanes[k] = null;
    }

    // 3. Route parents. First parent stays in this column; extra parents (merges)
    //    reuse a lane already heading to them, else claim a free lane.
    const edges: LaneEdge[] = [];
    let hasBelowLineParent = false;
    c.parents.forEach((p, pi) => {
      let pc: number;
      if (pi === 0) {
        pc = col;
      } else {
        const existing = lanes.indexOf(p);
        pc = existing !== -1 ? existing : takeFreeLane();
      }
      lanes[pc] = p;
      const toRow = shaToRow.has(p) ? (shaToRow.get(p) as number) : null;
      if (toRow === null) {
        // Parent is below the rendered window — its edge descends to the ship
        // line and no in-window row will ever match it, so free the lane now
        // rather than letting a dangling reservation bloat the gutter forever.
        hasBelowLineParent = true;
        lanes[pc] = null;
      }
      edges.push({ toCol: pc, toRow, toSha: p });
    });

    // Root within the window (no parents at all): the lane terminates.
    if (c.parents.length === 0) lanes[col] = null;

    laneCount = Math.max(laneCount, lanes.length);
    rows.push({ commit: c, col, edges, hasBelowLineParent });
  }

  return { rows, laneCount: Math.max(1, laneCount), shaToRow };
}

/**
 * Sanity check the render against git's own ahead count. When the rendered node
 * count disagrees with `status.ahead`, the window is truncated or the base moved
 * — the caller shows a divergence banner rather than implying completeness.
 */
export function laneDivergence(
  rendered: number,
  ahead: number | undefined,
  truncated: boolean,
): { diverged: boolean; ahead: number; rendered: number } | null {
  if (ahead == null) return null;
  // Truncation is expected (paging), not divergence — only flag a genuine mismatch.
  if (truncated) return null;
  if (rendered === ahead) return null;
  return { diverged: true, ahead, rendered };
}

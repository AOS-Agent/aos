/**
 * briefStyle — the vocabulary of a compiled brief, translated for humans.
 *
 * The contract ships machine words (`not_started`, `duplicate_spine`,
 * `status_disagreement`). The operator never sees them. Every map here turns a
 * contract enum into plain English plus a design token.
 *
 * Tailwind JIT only generates classes it can see as complete static strings, so
 * every class below is spelled out in full — never built by interpolation.
 */

import type { Actor } from '@/hooks/useProjectBrief';

export interface Tone {
  text: string;
  bg: string;
  dot: string;
}

const TONE = {
  green:  { text: 'text-tag-green',  bg: 'bg-tag-green-bg',  dot: 'bg-tag-green' },
  yellow: { text: 'text-tag-yellow', bg: 'bg-tag-yellow-bg', dot: 'bg-tag-yellow' },
  gray:   { text: 'text-tag-gray',   bg: 'bg-tag-gray-bg',   dot: 'bg-tag-gray' },
  red:    { text: 'text-tag-red',    bg: 'bg-tag-red-bg',    dot: 'bg-tag-red' },
  blue:   { text: 'text-tag-blue',   bg: 'bg-tag-blue-bg',   dot: 'bg-tag-blue' },
  purple: { text: 'text-tag-purple', bg: 'bg-tag-purple-bg', dot: 'bg-tag-purple' },
  teal:   { text: 'text-tag-teal',   bg: 'bg-tag-teal-bg',   dot: 'bg-tag-teal' },
  pink:   { text: 'text-tag-pink',   bg: 'bg-tag-pink-bg',   dot: 'bg-tag-pink' },
  // The DESIGN "orange" token is repointed to warm-neutral sand (#A99E88).
  // There is no orange in the system; the name is legacy, the value is not.
  sand:   { text: 'text-tag-orange', bg: 'bg-tag-orange-bg', dot: 'bg-tag-orange' },
} as const satisfies Record<string, Tone>;

// ── Derived project state ───────────────────────────────────────────────────

const STATE: Record<string, { label: string; tone: Tone }> = {
  moving:      { label: 'Moving',      tone: TONE.green },
  warm:        { label: 'Warm',        tone: TONE.yellow },
  cold:        { label: 'Cold',        tone: TONE.gray },
  not_started: { label: 'Not started', tone: TONE.gray },
  blocked:     { label: 'Blocked',     tone: TONE.red },
  done:        { label: 'Done',        tone: TONE.blue },
};

export function stateLabel(state: string): string {
  return STATE[state]?.label ?? sentence(state);
}

export function stateTone(state: string): Tone {
  return STATE[state]?.tone ?? TONE.gray;
}

/** `not_started` reads as an absence, so it renders outlined rather than filled. */
export function stateIsHollow(state: string): boolean {
  return state === 'not_started' || state === 'cold';
}

/** What kind of signal last moved the project — for "last moved 3 days ago · a commit". */
export function activitySourceLabel(source: string): string {
  switch (source) {
    case 'task':    return 'a task update';
    case 'git':     return 'a commit';
    case 'session':  return 'a session';
    case 'handoff': return 'a handoff';
    default:        return '';
  }
}

// ── Phase state ─────────────────────────────────────────────────────────────

const PHASE_STATE: Record<string, { label: string; tone: Tone }> = {
  not_started: { label: 'Not started', tone: TONE.gray },
  in_progress: { label: 'In progress', tone: TONE.green },
  done:        { label: 'Done',        tone: TONE.blue },
  blocked:     { label: 'Blocked',     tone: TONE.red },
};

export function phaseStateLabel(state: string): string {
  return PHASE_STATE[state]?.label ?? sentence(state);
}

export function phaseStateTone(state: string): Tone {
  return PHASE_STATE[state]?.tone ?? TONE.gray;
}

// ── Conflicts ───────────────────────────────────────────────────────────────

const CONFLICT: Record<string, string> = {
  duplicate_spine:      'Duplicate work',
  orphan_task:          'Orphaned task',
  stale_doc:            'Doc out of date',
  untracked_repo:       'Repo not linked',
  status_disagreement:  'Sources disagree',
  no_body:              'No description',
};

export function conflictLabel(kind: string): string {
  return CONFLICT[kind] ?? sentence(kind);
}

export function conflictTone(severity: string): Tone {
  return severity === 'error' ? TONE.red : TONE.yellow;
}

// ── Artifacts ───────────────────────────────────────────────────────────────

const ARTIFACT: Record<string, { label: string; tone: Tone }> = {
  initiative: { label: 'Initiative', tone: TONE.purple },
  spec:       { label: 'Spec',       tone: TONE.blue },
  decision:   { label: 'Decision',   tone: TONE.sand },
  council:    { label: 'Council',    tone: TONE.sand },
  session:    { label: 'Session',    tone: TONE.teal },
  commit:     { label: 'Commit',     tone: TONE.gray },
  file:       { label: 'File',       tone: TONE.gray },
  deck:       { label: 'Deck',       tone: TONE.pink },
};

export function artifactLabel(kind: string): string {
  return ARTIFACT[kind]?.label ?? sentence(kind);
}

export function artifactTone(kind: string): Tone {
  return ARTIFACT[kind]?.tone ?? TONE.gray;
}

/** Browsable groupings — the filter row on the Artifacts section. */
export const ARTIFACT_GROUPS: { key: string; label: string; kinds: string[] }[] = [
  { key: 'docs',      label: 'Docs',      kinds: ['initiative', 'spec', 'file', 'deck'] },
  { key: 'decisions', label: 'Decisions', kinds: ['decision', 'council'] },
  { key: 'sessions',  label: 'Sessions',  kinds: ['session'] },
  { key: 'commits',   label: 'Commits',   kinds: ['commit'] },
];

// ── Timeline events ─────────────────────────────────────────────────────────

const EVENT_TONE: Record<string, Tone> = {
  task_done:    TONE.green,
  task_started: TONE.blue,
  task_created: TONE.gray,
  commit:       TONE.purple,
  session:      TONE.teal,
  handoff:      TONE.yellow,
  decision:     TONE.sand,
};

export function eventTone(kind: string): Tone {
  return EVENT_TONE[kind] ?? TONE.gray;
}

// ── Attribution ─────────────────────────────────────────────────────────────

/**
 * Who did it, in plain English. The operator is "You". An unresolved actor is
 * "Someone" — the contract is explicit that `unknown` is an honest answer and
 * must never be dressed up as a guess.
 */
export function actorLabel(actor: Actor | null | undefined): string {
  if (!actor || !actor.kind || actor.kind === 'unknown') return 'Someone';
  switch (actor.kind) {
    case 'operator': return 'You';
    case 'cron':     return 'A schedule';
    case 'import':   return 'An import';
    default:         return sentence(actor.name || 'someone');
  }
}

/**
 * The compiler already writes timeline lines like `Chief completed "…"`. When it
 * does, the actor chip would just repeat it — so suppress the chip.
 */
export function textNamesActor(text: string, label: string): boolean {
  return text.trim().toLowerCase().startsWith(label.toLowerCase());
}

// ── Paths ───────────────────────────────────────────────────────────────────

/**
 * Vault-relative path readable through `/api/knowledge/library/file`, or null.
 * That endpoint only serves `knowledge/**`, so session logs and repo files
 * resolve to null and render as a path rather than a broken "open" affordance.
 */
export function vaultDocPath(path: string): string | null {
  if (!path) return null;
  let p = path.replace(/^\/+/, '').split('#')[0];
  if (p.startsWith('vault/')) p = p.slice('vault/'.length);
  if (!p.startsWith('knowledge/') || p.includes('..')) return null;
  return p;
}

export function baseName(path: string): string {
  const clean = (path || '').split('#')[0];
  return clean.slice(clean.lastIndexOf('/') + 1) || clean;
}

/** A conflict ref that is a task id (`hre#1.3`) rather than a file path. */
export function isTaskRef(ref: string): boolean {
  return /^[a-z0-9_-]+#[\d.]+$/i.test(ref.trim());
}

// ── Shared ──────────────────────────────────────────────────────────────────

/** `not_started` → `Not started`, `session-close` → `Session close`. */
function sentence(raw: string): string {
  const words = (raw || '').replace(/[_-]+/g, ' ').trim();
  if (!words) return '';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

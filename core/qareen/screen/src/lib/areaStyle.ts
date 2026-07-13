/**
 * Area identity — every Goal is an "area" (AOS, Deen Over Dunya, …) and gets a
 * distinct color from the DESIGN tag palette. The color propagates everywhere
 * (area headers, project cards, task rows) so affiliation is always visible.
 *
 * Tailwind JIT only generates classes it sees as COMPLETE static strings, so the
 * tones below are spelled out in full — never build `text-${x}` dynamically.
 */
import type { Task, Project } from '@/hooks/useWork';

export interface AreaTone {
  key: string;
  text: string; // text + icon color
  dot: string;  // solid fill (dot / progress bar) via currentColor
  bg: string;   // faint wash background for chips
  ring: string; // subtle left border
}

const TONES: Record<string, AreaTone> = {
  green:  { key: 'green',  text: 'text-tag-green',  dot: 'text-tag-green bg-current',  bg: 'bg-tag-green-bg',  ring: 'border-tag-green' },
  purple: { key: 'purple', text: 'text-tag-purple', dot: 'text-tag-purple bg-current', bg: 'bg-tag-purple-bg', ring: 'border-tag-purple' },
  blue:   { key: 'blue',   text: 'text-tag-blue',   dot: 'text-tag-blue bg-current',   bg: 'bg-tag-blue-bg',   ring: 'border-tag-blue' },
  orange: { key: 'orange', text: 'text-tag-orange', dot: 'text-tag-orange bg-current', bg: 'bg-tag-orange-bg', ring: 'border-tag-orange' },
  teal:   { key: 'teal',   text: 'text-tag-teal',   dot: 'text-tag-teal bg-current',   bg: 'bg-tag-teal-bg',   ring: 'border-tag-teal' },
  pink:   { key: 'pink',   text: 'text-tag-pink',   dot: 'text-tag-pink bg-current',   bg: 'bg-tag-pink-bg',   ring: 'border-tag-pink' },
  gray:   { key: 'gray',   text: 'text-tag-gray',   dot: 'text-tag-gray bg-current',   bg: 'bg-tag-gray-bg',   ring: 'border-tag-gray' },
};

const PALETTE = ['green', 'purple', 'blue', 'orange', 'teal', 'pink'];

// Stable, meaningful overrides for the known areas.
const OVERRIDES: Record<string, string> = {
  'dod-launch': 'green',          // Deen Over Dunya — deen, growth
  'aos-infrastructure': 'purple', // AOS — the agentic system
};

/** Tone for a goal/area. Falls back to a stable palette slot by index. */
export function areaTone(goalId: string | null | undefined, index = 0): AreaTone {
  if (goalId && OVERRIDES[goalId]) return TONES[OVERRIDES[goalId]];
  if (!goalId) return TONES.gray;
  return TONES[PALETTE[index % PALETTE.length]];
}

/** Resolve a task's area (goal id) via its project. */
export function taskGoalId(task: Task, projects: Project[]): string | null {
  const p = projects.find(pr => pr.id === task.project || pr.title === task.project);
  return p?.goal ?? null;
}

/**
 * Map an initiative's `project` frontmatter slug to a work GOAL id, so initiatives
 * inherit the right area color. Returns null when the initiative belongs to no
 * known area.
 */
export function initiativeGoalId(projectSlug: string | null | undefined): string | null {
  if (!projectSlug) return null;
  const s = projectSlug.trim().toLowerCase();
  if (s === 'aos') return 'aos-infrastructure';
  if (s === 'deenoverdunya' || s === 'dod' || s === 'deen-over-dunya' || s === 'deen') return 'dod-launch';
  return null;
}

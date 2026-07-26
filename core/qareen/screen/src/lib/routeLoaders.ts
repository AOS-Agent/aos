import type { ComponentType } from 'react';

type Loader = () => Promise<{ default: ComponentType<never> }>;

/**
 * Every code-split page, keyed by the URL that renders it.
 *
 * This is the single source of truth for route chunks: App.tsx builds its
 * React.lazy components from these loaders, and TransitionLink prefetches
 * from the same map. Keeping one map means a prefetch target can never
 * silently drift away from the route it is supposed to warm.
 */
export const PAGE_LOADERS = {
  '/': () => import('@/pages/Companion'),
  '/companion/session': () => import('@/pages/CompanionSession'),
  '/work': () => import('@/pages/Work'),
  '/people': () => import('@/pages/People'),
  '/timeline': () => import('@/pages/Days'),
  '/chat': () => import('@/pages/Chat'),
  '/system': () => import('@/pages/System'),
  '/settings': () => import('@/pages/Settings'),
  '/agents': () => import('@/pages/Agents'),
  '/agents/:id': () => import('@/pages/AgentConfig'),
  '/skills': () => import('@/pages/Skills'),
  '/org': () => import('@/pages/Org'),
  '/shipments': () => import('@/pages/Shipments'),
  '/shipments/eval': () => import('@/pages/ShipmentsEval'),
  '/shipments/:id': () => import('@/pages/ShipmentDetail'),
  '/knowledge': () => import('@/pages/Knowledge'),
  '/intelligence/sources': () => import('@/pages/IntelligenceSources'),
  '/intelligence/:id': () => import('@/pages/IntelligenceDetail'),
  '/sessions': () => import('@/pages/Sessions'),
  '/sessions/:id': () => import('@/pages/SessionDetail'),
  '/calendar': () => import('@/pages/Calendar'),
} satisfies Record<string, Loader>;

export type RoutePath = keyof typeof PAGE_LOADERS;

/** Module-level cache so repeated prefetches share one in-flight promise. */
const inFlight = new Map<string, Promise<unknown>>();

/**
 * Resolve an href to its chunk loader. Exact match wins; otherwise the
 * longest registered prefix does, so `/knowledge/library` warms Knowledge
 * and `/timeline/2026-07-26` warms Days.
 */
function loaderFor(href: string): Loader | undefined {
  const loaders = PAGE_LOADERS as Record<string, Loader>;
  if (loaders[href]) return loaders[href];

  let best: string | undefined;
  for (const path of Object.keys(loaders)) {
    if (path === '/' || path.includes(':')) continue;
    if (href.startsWith(path + '/') && (!best || path.length > best.length)) best = path;
  }
  return best ? loaders[best] : undefined;
}

/**
 * Start downloading a route's chunk. Safe to call repeatedly (on hover, on
 * focus, on click) — the promise is cached and a failure is swallowed so a
 * prefetch can never break navigation.
 */
export function prefetchRoute(href: string): Promise<unknown> {
  const cached = inFlight.get(href);
  if (cached) return cached;

  const loader = loaderFor(href);
  if (!loader) return Promise.resolve();

  const p = loader().catch(() => {
    // Let a later attempt retry rather than caching the failure forever.
    inFlight.delete(href);
  });
  inFlight.set(href, p);
  return p;
}

import { createElement, lazy, useState, type ComponentType } from 'react';

type PageComponent = ComponentType<Record<string, never>>;
type Loader = () => Promise<{ default: PageComponent }>;

/**
 * Every code-split page, keyed by the URL that renders it.
 *
 * This is the single source of truth for route chunks: App.tsx builds its
 * route components from these loaders, and TransitionLink prefetches from the
 * same map. Keeping one map means a prefetch target can never silently drift
 * away from the route it is supposed to warm.
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
  '/converse': () => import('@/pages/Converse'),
  '/calendar': () => import('@/pages/Calendar'),
} satisfies Record<string, Loader>;

export type RoutePath = keyof typeof PAGE_LOADERS;

const loaders = PAGE_LOADERS as Record<string, Loader>;

/** Loader promises, so repeated prefetches share one request. */
const inFlight = new Map<string, Promise<unknown>>();

/** Modules that have finished loading, keyed by route path. */
const ready = new Map<string, PageComponent>();

/**
 * Resolve an href to its route key. Exact match wins; otherwise the longest
 * registered prefix does, so `/knowledge/library` warms Knowledge and
 * `/timeline/2026-07-26` warms Days.
 */
function keyFor(href: string): string | undefined {
  if (loaders[href]) return href;

  let best: string | undefined;
  for (const path of Object.keys(loaders)) {
    if (path === '/' || path.includes(':')) continue;
    if (href.startsWith(path + '/') && (!best || path.length > best.length)) best = path;
  }
  return best;
}

/**
 * Start downloading a route's chunk and remember the resolved module. Safe to
 * call repeatedly (hover, focus, click) — the promise is cached, and failures
 * are swallowed so a prefetch can never break navigation.
 */
export function prefetchRoute(href: string): Promise<unknown> {
  const key = keyFor(href);
  if (!key) return Promise.resolve();

  const cached = inFlight.get(key);
  if (cached) return cached;

  const p = loaders[key]()
    .then((m) => { ready.set(key, m.default); })
    .catch(() => {
      // Drop the cache entry so a later attempt can retry rather than
      // caching the failure for the lifetime of the tab.
      inFlight.delete(key);
    });
  inFlight.set(key, p);
  return p;
}

/** True once a route can render without suspending. */
export function isRouteReady(href: string): boolean {
  const key = keyFor(href);
  return !!key && ready.has(key);
}

/**
 * Build the component for a route.
 *
 * The wrapper exists to serve the View Transitions path. `React.lazy` always
 * suspends on first render — even when the chunk is already in the module
 * cache, it resolves its payload in a microtask. Inside
 * `startViewTransition(() => flushSync(...))` that microtask never gets to
 * run, so React commits the Suspense fallback and the browser snapshots a
 * spinner instead of the page. When the module is already resolved we hand
 * back the real component, which renders synchronously and lets the
 * transition capture actual content.
 *
 * The choice is frozen per mount via useState so the element type never
 * changes mid-mount — swapping it would remount the page and refetch.
 */
export function routeComponent(path: RoutePath): PageComponent {
  const key = path as string;
  const Lazy = lazy(async () => {
    const m = await loaders[key]();
    ready.set(key, m.default);
    return m;
  });

  return function Route(props: Record<string, never>) {
    const [Comp] = useState<PageComponent>(() => ready.get(key) ?? Lazy);
    return createElement(Comp, props);
  };
}

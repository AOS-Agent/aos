import { useNavigate } from 'react-router-dom';
import { flushSync } from 'react-dom';
import { type ReactNode, type MouseEvent, useCallback } from 'react';
import { prefetchRoute } from '@/lib/routeLoaders';

interface TransitionLinkProps {
  href: string;
  children: ReactNode;
  className?: string;
  onClick?: (e: MouseEvent) => void;
  prefetch?: boolean;
  [key: string]: unknown;
}

/**
 * How long we are willing to hold the click waiting for the route's chunk
 * before giving up on the crossfade. A view transition freezes the screen on
 * a snapshot of the old page, so freezing while a chunk is still downloading
 * is the worst of both worlds: the URL changes and the pixels don't. Past
 * this budget we navigate plainly — a hard cut beats a frozen screen.
 */
const CHUNK_BUDGET_MS = 250;

/**
 * A link that wraps in-app navigation in the View Transitions API.
 *
 * Two things have to be true for the transition to look right, and the naive
 * `startViewTransition(() => navigate(href))` gets both wrong:
 *
 *   1. The chunk must already be loaded. Every page here is React.lazy, so a
 *      cold chunk means React has nothing to paint when the browser captures
 *      the "after" snapshot — it crossfades the old page into an empty frame
 *      and the operator sees the old screen under a new URL.
 *   2. The DOM must be committed *inside* the callback. `navigate()` only
 *      schedules a React state update; the browser treats the callback as
 *      done the moment it returns and snapshots a DOM that hasn't changed
 *      yet. `flushSync` forces the commit before we hand control back.
 *
 * Falls back to plain navigation when the API is missing or the chunk is slow.
 */
export default function TransitionLink({
  href,
  children,
  className,
  onClick,
  prefetch = true,
  ...rest
}: TransitionLinkProps) {
  const navigate = useNavigate();

  // Warm the chunk on intent, so the click itself usually waits on nothing.
  const warm = useCallback(() => {
    if (prefetch) prefetchRoute(href);
  }, [href, prefetch]);

  const handleClick = useCallback(
    async (e: MouseEvent<HTMLAnchorElement>) => {
      // Let the browser handle modified clicks (new tab, etc.) natively.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;

      e.preventDefault();
      onClick?.(e);

      const ready = await Promise.race([
        prefetchRoute(href).then(() => true),
        new Promise<boolean>((r) => setTimeout(() => r(false), CHUNK_BUDGET_MS)),
      ]);

      if (!ready || !document.startViewTransition) {
        navigate(href);
        return;
      }

      document.startViewTransition(() => {
        flushSync(() => navigate(href));
      });
    },
    [href, onClick, navigate],
  );

  return (
    <a
      href={href}
      className={className}
      onClick={handleClick}
      onMouseEnter={warm}
      onFocus={warm}
      onTouchStart={warm}
      {...rest}
    >
      {children}
    </a>
  );
}

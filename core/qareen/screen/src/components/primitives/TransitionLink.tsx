import { useNavigate } from 'react-router-dom';
import { type ReactNode, type MouseEvent, useCallback } from 'react';

interface TransitionLinkProps {
  href: string;
  children: ReactNode;
  className?: string;
  onClick?: (e: MouseEvent) => void;
  prefetch?: boolean;
  [key: string]: unknown;
}

/**
 * A link component that wraps navigation in the View Transitions API.
 * Falls back to instant navigation if the API isn't supported.
 *
 * Use this instead of <Link> for in-app navigation to get
 * smooth crossfade and shared element transitions.
 */
export default function TransitionLink({
  href,
  children,
  className,
  onClick,
  prefetch,
  ...rest
}: TransitionLinkProps) {
  const navigate = useNavigate();

  const handleClick = useCallback(
    (e: MouseEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      onClick?.(e);

      const doNavigate = () => navigate(href);

      if (document.startViewTransition) {
        document.startViewTransition(doNavigate);
      } else {
        doNavigate();
      }
    },
    [href, onClick, navigate],
  );

  return (
    <a href={href} className={className} onClick={handleClick} {...rest}>
      {children}
    </a>
  );
}

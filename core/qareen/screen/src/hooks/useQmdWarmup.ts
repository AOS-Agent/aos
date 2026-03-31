import { useEffect } from 'react';

/**
 * Fires a lightweight warmup request to the API on mount.
 * In the Vite web version, this just pings the health endpoint
 * to ensure connectivity rather than running qmd directly.
 */
export function useQmdWarmup() {
  useEffect(() => {
    fetch('/api/health').catch(() => {});
  }, []);
}

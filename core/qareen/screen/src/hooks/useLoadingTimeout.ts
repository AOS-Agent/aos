import { useEffect, useState } from 'react';

// ---------------------------------------------------------------------------
// useLoadingTimeout — returns true once `active` has stayed true continuously
// for `ms` milliseconds. The escape hatch for indefinite skeletons: gate a
// loading skeleton on `!timedOut`, and fall back to an explicit error/retry
// state once this fires, so a hung fetch can never leave a skeleton pulsing
// forever (DESIGN.md → Loading & Empty States).
// ---------------------------------------------------------------------------

export function useLoadingTimeout(active: boolean, ms = 8000): boolean {
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    if (!active) {
      setTimedOut(false);
      return;
    }
    setTimedOut(false);
    const t = setTimeout(() => setTimedOut(true), ms);
    return () => clearTimeout(t);
  }, [active, ms]);

  return timedOut;
}

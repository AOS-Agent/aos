'use client';

import { Network } from 'lucide-react';

export default function TeamPage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center">
      <Network className="w-12 h-12 text-text-quaternary mb-4" />
      <h1 className="text-xl font-bold text-text tracking-[-0.013em] mb-2">Team</h1>
      <p className="text-sm text-text-secondary max-w-[400px]">
        Your agent org chart — who does what, trust levels, and mission alignment.
      </p>
      <span className="mt-4 text-[11px] text-text-quaternary font-medium uppercase tracking-wider">
        Coming in Phase 4
      </span>
    </div>
  );
}

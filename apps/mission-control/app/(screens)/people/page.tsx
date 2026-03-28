'use client';

import { Users } from 'lucide-react';

export default function PeoplePage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center">
      <Users className="w-12 h-12 text-text-quaternary mb-4" />
      <h1 className="text-xl font-bold text-text tracking-[-0.013em] mb-2">People</h1>
      <p className="text-sm text-text-secondary max-w-[400px]">
        Manage contacts and relationships across your network.
      </p>
      <span className="mt-4 text-[11px] text-text-quaternary font-medium uppercase tracking-wider">
        Coming in Phase 4
      </span>
    </div>
  );
}

'use client';

import { Building2 } from 'lucide-react';

export default function OfficePage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center">
      <Building2 className="w-12 h-12 text-text-quaternary mb-4" />
      <h1 className="text-xl font-bold text-text tracking-[-0.013em] mb-2">Office</h1>
      <p className="text-sm text-text-secondary max-w-[400px]">
        Watch your agents work in a pixel art office. Fun meets function.
      </p>
      <span className="mt-4 text-[11px] text-text-quaternary font-medium uppercase tracking-wider">
        Coming in Phase 4
      </span>
    </div>
  );
}

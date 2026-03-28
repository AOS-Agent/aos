'use client';

import { Scale } from 'lucide-react';

export default function CouncilPage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center">
      <Scale className="w-12 h-12 text-text-quaternary mb-4" />
      <h1 className="text-xl font-bold text-text tracking-[-0.013em] mb-2">Council</h1>
      <p className="text-sm text-text-secondary max-w-[400px]">
        View deliberation logs and decision records from multi-perspective analysis.
      </p>
      <span className="mt-4 text-[11px] text-text-quaternary font-medium uppercase tracking-wider">
        Coming in Phase 3
      </span>
    </div>
  );
}

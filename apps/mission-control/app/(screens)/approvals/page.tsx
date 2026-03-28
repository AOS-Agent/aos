'use client';

import { ShieldCheck } from 'lucide-react';

export default function ApprovalsPage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center">
      <ShieldCheck className="w-12 h-12 text-text-quaternary mb-4" />
      <h1 className="text-xl font-bold text-text tracking-[-0.013em] mb-2">Approvals</h1>
      <p className="text-sm text-text-secondary max-w-[400px]">
        Review and approve actions your agents have taken or want to take.
      </p>
      <span className="mt-4 text-[11px] text-text-quaternary font-medium uppercase tracking-wider">
        Coming in Phase 3
      </span>
    </div>
  );
}

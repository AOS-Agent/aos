'use client';

import { FolderKanban } from 'lucide-react';

export default function ProjectsPage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center">
      <FolderKanban className="w-12 h-12 text-text-quaternary mb-4" />
      <h1 className="text-xl font-bold text-text tracking-[-0.013em] mb-2">Projects</h1>
      <p className="text-sm text-text-secondary max-w-[400px]">
        Track major projects, progress, and linked tasks across your work.
      </p>
      <span className="mt-4 text-[11px] text-text-quaternary font-medium uppercase tracking-wider">
        Coming in Phase 3
      </span>
    </div>
  );
}

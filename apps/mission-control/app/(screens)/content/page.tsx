'use client';

import { FileText } from 'lucide-react';

export default function ContentPage() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center">
      <FileText className="w-12 h-12 text-text-quaternary mb-4" />
      <h1 className="text-xl font-bold text-text tracking-[-0.013em] mb-2">Content</h1>
      <p className="text-sm text-text-secondary max-w-[400px]">
        Browse content created by your agents — drafts, extracts, newsletters, and media.
      </p>
      <span className="mt-4 text-[11px] text-text-quaternary font-medium uppercase tracking-wider">
        Coming in Phase 2
      </span>
    </div>
  );
}

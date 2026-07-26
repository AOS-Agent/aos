/**
 * ArtifactDrawer — read a vault document without leaving the project.
 *
 * Uses the existing `/api/knowledge/library/file` reader (the same endpoint the
 * Knowledge Library uses). That endpoint only serves `knowledge/**`, so callers
 * gate on `vaultDocPath()` and never offer this for sessions or repo files.
 */

import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { MarkdownRenderer } from '@/components/primitives';
import { formatDate } from '@/lib/format';

interface DocResponse {
  path: string;
  frontmatter: Record<string, unknown>;
  body: string;
  modified?: string;
}

export function ArtifactDrawer({
  path,
  title,
  onClose,
}: {
  path: string;
  title: string;
  onClose: () => void;
}) {
  const [doc, setDoc] = useState<DocResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [closing, setClosing] = useState(false);

  const startClose = () => setClosing(true);

  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') startClose(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, []);

  // The parent mounts this with `key={path}`, so a different document is a
  // fresh component — no reset needed here, and no setState in the effect body.
  useEffect(() => {
    let alive = true;
    fetch(`/api/knowledge/library/file?path=${encodeURIComponent(path)}`, {
      signal: AbortSignal.timeout(10_000),
    })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(d => { if (alive) setDoc(d as DocResponse); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [path]);

  const stage = doc?.frontmatter?.stage;
  const docType = doc?.frontmatter?.type;

  return (
    <div
      className="fixed inset-0 z-[700] flex justify-end"
      onAnimationEnd={() => { if (closing) onClose(); }}
    >
      <style>{`
        @keyframes artIn{from{opacity:0}to{opacity:1}}
        @keyframes artOut{from{opacity:1}to{opacity:0}}
        @keyframes artSlideIn{from{transform:translateX(24px);opacity:0}to{transform:none;opacity:1}}
        @keyframes artSlideOut{from{transform:none;opacity:1}to{transform:translateX(24px);opacity:0}}
      `}</style>

      <div
        onClick={startClose}
        className={`absolute inset-0 bg-bg/70 backdrop-blur-sm ${
          closing ? 'animate-[artOut_160ms_ease-in]' : 'animate-[artIn_180ms_ease-out]'
        }`}
      />

      <div
        className={`relative h-full w-full max-w-[720px] bg-bg-panel border-l border-border-secondary flex flex-col ${
          closing ? 'animate-[artSlideOut_160ms_ease-in]' : 'animate-[artSlideIn_180ms_ease-out]'
        }`}
      >
        <div className="flex items-start gap-3 px-6 py-4 border-b border-border shrink-0">
          <div className="min-w-0 flex-1">
            <h2 className="font-serif text-[20px] leading-[1.3] text-text truncate">
              {String(doc?.frontmatter?.title ?? title)}
            </h2>
            <p className="text-[11px] font-mono text-text-quaternary truncate mt-0.5">{path}</p>
          </div>
          <button
            onClick={startClose}
            aria-label="Close document"
            className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-text-tertiary hover:text-text hover:bg-bg-tertiary cursor-pointer transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {(docType || stage || doc?.modified) && (
          <div className="flex items-center gap-3 px-6 py-2 border-b border-border text-[11px] text-text-quaternary shrink-0">
            {docType ? <span>{String(docType)}</span> : null}
            {stage != null ? <span>Stage {String(stage)}</span> : null}
            {doc?.modified ? <span>Updated {formatDate(doc.modified)}</span> : null}
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {!doc && !failed && (
            <div className="space-y-3">
              {[0, 1, 2, 3, 4].map(i => (
                <div key={i} className="h-4 rounded bg-bg-tertiary animate-pulse" style={{ width: `${90 - i * 9}%` }} />
              ))}
            </div>
          )}
          {failed && (
            <div className="py-10">
              <p className="text-[14px] font-[510] text-text-tertiary mb-1">Couldn't open this document</p>
              <p className="text-[12px] text-text-quaternary">
                The brief points at <span className="font-mono">{path}</span>, but the vault reader
                couldn't return it. The file may have been moved or renamed since the last compile.
              </p>
            </div>
          )}
          {doc && <MarkdownRenderer content={doc.body} compact />}
        </div>
      </div>
    </div>
  );
}

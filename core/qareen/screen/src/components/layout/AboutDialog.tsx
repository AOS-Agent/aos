import { useEffect, useState } from 'react';
import { X } from 'lucide-react';

interface AboutDialogProps {
  open: boolean;
  onClose: () => void;
}

export default function AboutDialog({ open, onClose }: AboutDialogProps) {
  const [version, setVersion] = useState('');
  const [hostname, setHostname] = useState('');

  useEffect(() => {
    if (!open) return;
    fetch('/api/version')
      .then(r => r.json())
      .then(data => {
        setVersion(data.version || data || '');
        setHostname(data.hostname || '');
      })
      .catch(() => setVersion('unknown'));
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />

      <div className="relative w-[340px] bg-bg-secondary border border-border rounded-lg shadow-2xl overflow-hidden">
        <button
          type="button"
          onClick={onClose}
          className="absolute top-3 right-3 w-6 h-6 flex items-center justify-center rounded-sm text-text-tertiary hover:text-text-secondary hover:bg-hover transition-colors"
          aria-label="Close"
        >
          <X className="w-3.5 h-3.5" />
        </button>

        <div className="px-8 pt-8 pb-6 flex flex-col items-center text-center">
          <div className="w-16 h-16 rounded-2xl bg-accent/10 flex items-center justify-center mb-4">
            <span className="text-2xl font-bold text-accent">A</span>
          </div>

          <h2 className="text-lg font-semibold text-text tracking-tight">
            Qareen
          </h2>
          <p className="text-xs text-text-tertiary mt-0.5">
            AOS — Agentic Operating System
          </p>

          <div className="mt-5 w-full space-y-2 text-xs">
            <div className="flex justify-between text-text-tertiary">
              <span>Version</span>
              <span className="text-text-secondary font-mono">{version || '...'}</span>
            </div>
            {hostname && (
              <div className="flex justify-between text-text-tertiary">
                <span>Host</span>
                <span className="text-text-secondary font-mono">{hostname}</span>
              </div>
            )}
            <div className="flex justify-between text-text-tertiary">
              <span>Runtime</span>
              <span className="text-text-secondary font-mono">Vite + React</span>
            </div>
            <div className="flex justify-between text-text-tertiary">
              <span>Platform</span>
              <span className="text-text-secondary font-mono">Web</span>
            </div>
          </div>
        </div>

        <div className="px-8 py-3 border-t border-border bg-bg-tertiary/30">
          <p className="text-[10px] text-text-quaternary text-center">
            Built by Hisham. Powered by Anthropic.
          </p>
        </div>
      </div>
    </div>
  );
}

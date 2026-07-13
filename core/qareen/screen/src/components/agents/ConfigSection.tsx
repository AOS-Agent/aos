import { useState, useRef, useEffect, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

interface ConfigSectionProps {
  title: string;
  icon: ReactNode;
  defaultOpen?: boolean;
  disabled?: boolean;
  children: ReactNode;
}

export default function ConfigSection({
  title,
  icon,
  defaultOpen = false,
  disabled = false,
  children,
}: ConfigSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const contentRef = useRef<HTMLDivElement>(null);
  const [contentHeight, setContentHeight] = useState<number | undefined>(
    defaultOpen ? undefined : 0,
  );

  useEffect(() => {
    if (!contentRef.current) return;
    if (open) {
      setContentHeight(contentRef.current.scrollHeight);
      // After transition, set to auto for dynamic content
      const timer = setTimeout(() => setContentHeight(undefined), 160);
      return () => clearTimeout(timer);
    } else {
      // Measure current height first, then collapse
      setContentHeight(contentRef.current.scrollHeight);
      requestAnimationFrame(() => {
        setContentHeight(0);
      });
    }
  }, [open]);

  return (
    <div className="border-b border-border/40 last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 py-4 cursor-pointer group"
      >
        <span className="text-text-quaternary shrink-0">{icon}</span>
        <span className="text-[12px] font-[590] uppercase tracking-[0.05em] text-text-tertiary flex-1 text-left">
          {title}
        </span>
        {disabled && (
          <span className="text-[10px] font-[510] text-purple/60 uppercase tracking-wider mr-2">
            Read-only
          </span>
        )}
        <ChevronDown
          className={`w-3.5 h-3.5 text-text-quaternary transition-transform duration-150 ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>
      <div
        ref={contentRef}
        className="overflow-hidden transition-[max-height] duration-150 ease-out"
        style={{ maxHeight: contentHeight === undefined ? 'none' : contentHeight }}
      >
        <div className="pb-5">{children}</div>
      </div>
    </div>
  );
}

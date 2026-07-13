import { useState, useRef, useEffect } from 'react';
import { ChevronDown } from 'lucide-react';

interface SelectFieldProps {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  disabled?: boolean;
}

export default function SelectField({
  label,
  value,
  options,
  onChange,
  disabled = false,
}: SelectFieldProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <label className="block text-[11px] font-[510] text-text-quaternary mb-1.5">
        {label}
      </label>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between h-8 px-3 rounded-md border border-border-secondary bg-bg-secondary text-[12px] text-text-secondary cursor-pointer disabled:cursor-default disabled:opacity-50 transition-colors hover:border-border-tertiary"
        style={{ transitionDuration: '80ms' }}
      >
        <span className="truncate">{selected?.label ?? (value || '(none)')}</span>
        <ChevronDown
          className={`w-3.5 h-3.5 text-text-quaternary shrink-0 ml-2 transition-transform duration-150 ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>

      {open && !disabled && (
        <div className="absolute top-full left-0 mt-1 w-full rounded-lg border border-border-secondary bg-bg-secondary shadow-lg z-30 overflow-hidden">
          <div className="max-h-[240px] overflow-y-auto py-1">
            {options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
                className={`w-full text-left px-3 py-1.5 text-[12px] cursor-pointer transition-colors ${
                  opt.value === value
                    ? 'text-accent bg-accent/5'
                    : 'text-text-secondary hover:bg-hover'
                }`}
                style={{ transitionDuration: '80ms' }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

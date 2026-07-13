import { useState, useRef, useEffect, useCallback } from 'react';
import { X, Plus, Search } from 'lucide-react';

interface TagPickerProps {
  selected: string[];
  available: string[];
  onChange: (v: string[]) => void;
  disabled?: boolean;
  labels?: Record<string, string>;
}

export default function TagPicker({
  selected,
  available,
  onChange,
  disabled = false,
  labels,
}: TagPickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const unselected = available.filter(
    (item) =>
      !selected.includes(item) &&
      (item.toLowerCase().includes(search.toLowerCase()) ||
        labels?.[item]?.toLowerCase().includes(search.toLowerCase())),
  );

  const handleRemove = useCallback(
    (item: string) => {
      if (disabled) return;
      onChange(selected.filter((s) => s !== item));
    },
    [disabled, onChange, selected],
  );

  const handleAdd = useCallback(
    (item: string) => {
      if (disabled) return;
      onChange([...selected, item]);
      setSearch('');
    },
    [disabled, onChange, selected],
  );

  // Click outside to close
  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch('');
      }
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  // Auto-focus search on open
  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open]);

  // Check if tools show "All tools" badge
  const isAllTools = selected.includes('*');

  return (
    <div className="relative" ref={dropdownRef}>
      <div className="flex flex-wrap items-center gap-1.5 min-h-[32px]">
        {isAllTools ? (
          <span className="inline-flex items-center h-6 px-2.5 rounded text-[11px] font-[510] text-accent bg-accent/10">
            All tools
          </span>
        ) : (
          selected.map((item) => (
            <span
              key={item}
              className="inline-flex items-center gap-1 h-6 px-2 rounded text-[11px] font-mono text-text-tertiary bg-[rgba(255,245,235,0.04)]"
            >
              {labels?.[item] ?? item}
              {!disabled && (
                <button
                  type="button"
                  onClick={() => handleRemove(item)}
                  className="text-text-quaternary hover:text-text-tertiary cursor-pointer transition-colors"
                  style={{ transitionDuration: '80ms' }}
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </span>
          ))
        )}

        {!disabled && !isAllTools && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="inline-flex items-center justify-center w-6 h-6 rounded bg-[rgba(255,245,235,0.04)] hover:bg-[rgba(255,245,235,0.08)] text-text-quaternary hover:text-text-tertiary cursor-pointer transition-colors"
            style={{ transitionDuration: '80ms' }}
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {open && !disabled && (
        <div className="absolute top-full left-0 mt-2 w-64 rounded-lg border border-border-secondary bg-bg-secondary shadow-lg z-30 overflow-hidden">
          <div className="relative px-2 pt-2 pb-1">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-quaternary" />
            <input
              ref={inputRef}
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search..."
              className="w-full h-7 pl-7 pr-2 rounded bg-bg-tertiary border-none text-[11px] text-text placeholder:text-text-quaternary outline-none"
            />
          </div>
          <div className="max-h-[200px] overflow-y-auto px-1 pb-1">
            {unselected.length === 0 ? (
              <p className="text-[11px] text-text-quaternary px-3 py-3">
                No items available
              </p>
            ) : (
              unselected.map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => handleAdd(item)}
                  className="w-full text-left px-3 py-1.5 rounded text-[11px] text-text-secondary hover:bg-hover cursor-pointer transition-colors"
                  style={{ transitionDuration: '80ms' }}
                >
                  {labels?.[item] ? (
                    <span className="flex items-center gap-2">
                      <span className="font-[460]">{labels[item]}</span>
                      <span className="font-mono text-text-quaternary text-[10px]">{item}</span>
                    </span>
                  ) : (
                    <span className="font-mono">{item}</span>
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

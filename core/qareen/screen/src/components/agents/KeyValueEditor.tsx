import { useCallback } from 'react';
import { X, Plus } from 'lucide-react';

interface KeyValueEditorProps {
  entries: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  disabled?: boolean;
}

export default function KeyValueEditor({
  entries,
  onChange,
  disabled = false,
}: KeyValueEditorProps) {
  const pairs = Object.entries(entries);

  const handleKeyChange = useCallback(
    (oldKey: string, newKey: string) => {
      if (disabled) return;
      const next: Record<string, string> = {};
      for (const [k, v] of Object.entries(entries)) {
        next[k === oldKey ? newKey : k] = v;
      }
      onChange(next);
    },
    [disabled, entries, onChange],
  );

  const handleValueChange = useCallback(
    (key: string, newValue: string) => {
      if (disabled) return;
      onChange({ ...entries, [key]: newValue });
    },
    [disabled, entries, onChange],
  );

  const handleRemove = useCallback(
    (key: string) => {
      if (disabled) return;
      const next = { ...entries };
      delete next[key];
      onChange(next);
    },
    [disabled, entries, onChange],
  );

  const handleAdd = useCallback(() => {
    if (disabled) return;
    // Find a unique key name
    let n = 1;
    let key = 'key';
    while (key in entries) {
      key = `key_${n}`;
      n++;
    }
    onChange({ ...entries, [key]: '' });
  }, [disabled, entries, onChange]);

  return (
    <div className="space-y-2">
      {pairs.map(([key, val]) => (
        <div key={key} className="flex items-center gap-2">
          <input
            type="text"
            value={key}
            onChange={(e) => handleKeyChange(key, e.target.value)}
            disabled={disabled}
            placeholder="key"
            className="flex-1 h-7 px-2.5 rounded border border-border-secondary bg-bg-secondary text-[11px] font-mono text-text-secondary placeholder:text-text-quaternary outline-none disabled:opacity-50 transition-colors focus:border-border-tertiary"
            style={{ transitionDuration: '80ms' }}
          />
          <input
            type="text"
            value={val}
            onChange={(e) => handleValueChange(key, e.target.value)}
            disabled={disabled}
            placeholder="value"
            className="flex-1 h-7 px-2.5 rounded border border-border-secondary bg-bg-secondary text-[11px] font-mono text-text-secondary placeholder:text-text-quaternary outline-none disabled:opacity-50 transition-colors focus:border-border-tertiary"
            style={{ transitionDuration: '80ms' }}
          />
          {!disabled && (
            <button
              type="button"
              onClick={() => handleRemove(key)}
              className="w-7 h-7 flex items-center justify-center rounded text-text-quaternary hover:text-red hover:bg-red/5 cursor-pointer transition-colors"
              style={{ transitionDuration: '80ms' }}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      ))}

      {!disabled && (
        <button
          type="button"
          onClick={handleAdd}
          className="inline-flex items-center gap-1.5 h-7 px-3 rounded text-[11px] font-[510] text-text-quaternary hover:text-text-tertiary bg-[rgba(255,245,235,0.03)] hover:bg-[rgba(255,245,235,0.06)] cursor-pointer transition-colors"
          style={{ transitionDuration: '80ms' }}
        >
          <Plus className="w-3 h-3" /> Add parameter
        </button>
      )}
    </div>
  );
}

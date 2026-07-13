interface NumberFieldProps {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
  min?: number;
  max?: number;
  disabled?: boolean;
}

export default function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  disabled = false,
}: NumberFieldProps) {
  return (
    <div>
      <label className="block text-[11px] font-[510] text-text-quaternary mb-1.5">
        {label}
      </label>
      <input
        type="number"
        value={value ?? ''}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === '') {
            onChange(null);
          } else {
            const n = parseInt(raw, 10);
            if (!isNaN(n)) onChange(n);
          }
        }}
        min={min}
        max={max}
        disabled={disabled}
        placeholder="(none)"
        className="w-full h-8 px-3 rounded-md border border-border-secondary bg-bg-secondary text-[12px] font-mono text-text-secondary placeholder:text-text-quaternary outline-none disabled:opacity-50 disabled:cursor-default transition-colors focus:border-border-tertiary"
        style={{ transitionDuration: '80ms' }}
      />
    </div>
  );
}

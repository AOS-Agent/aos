interface TrustSliderProps {
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}

const TRUST_LABELS: Record<number, string> = {
  0: 'Observe',
  1: 'Surface',
  2: 'Draft',
  3: 'Act + Digest',
  4: 'Act + Audit',
  5: 'Autonomous',
};

export default function TrustSlider({
  value,
  onChange,
  disabled = false,
}: TrustSliderProps) {
  return (
    <div>
      <div className="flex gap-[3px] mb-2">
        {[0, 1, 2, 3, 4, 5].map((level) => (
          <button
            key={level}
            type="button"
            disabled={disabled}
            onClick={() => onChange(level)}
            className={`flex-1 h-2.5 rounded-full transition-colors cursor-pointer disabled:cursor-default ${
              level <= value
                ? 'bg-accent'
                : 'bg-[rgba(255,245,235,0.06)]'
            }`}
            style={{ transitionDuration: '80ms' }}
          />
        ))}
      </div>
      <p className="text-[11px] text-text-quaternary">
        Level {value} — {TRUST_LABELS[value] ?? 'Surface'}
      </p>
    </div>
  );
}

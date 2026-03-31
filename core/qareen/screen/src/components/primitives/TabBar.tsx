interface Tab {
  id: string;
  label: string;
}

interface TabBarProps {
  tabs: Tab[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}

export function TabBar({
  tabs,
  active,
  onChange,
  className = "",
}: TabBarProps) {
  return (
    <div
      className={`inline-flex items-center gap-px rounded-sm bg-bg-secondary p-0.5 ${className}`}
    >
      {tabs.map((tab) => {
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className={`
              px-3 h-7 rounded-xs text-[12px] font-[510]
              transition-colors
              ${
                isActive
                  ? "bg-bg-tertiary text-text shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
                  : "text-text-quaternary hover:text-text-tertiary"
              }
            `}
            style={{ transitionDuration: "var(--duration-instant)" }}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

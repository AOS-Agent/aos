import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// SectionNav — vertical list on desktop, horizontal on mobile.
// Each item is a page selector (not scroll target). Active = pill highlight.
// ---------------------------------------------------------------------------

export function SectionNav({
  sections,
  activeId,
  onSelect,
}: {
  sections: SettingsSection[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <>
      {/* ── Desktop: vertical list ── */}
      <nav className="hidden md:flex flex-col gap-0.5">
        {sections.map((s) => {
          const isActive = s.id === activeId;
          const Icon = s.icon;
          return (
            <button
              key={s.id}
              onClick={() => onSelect(s.id)}
              className={`
                flex items-center gap-2.5 px-3 py-1.5 rounded-[7px]
                text-[13px] font-[450] cursor-pointer text-left
                transition-all duration-150
                ${isActive
                  ? 'bg-[rgba(255,245,235,0.10)] text-text-secondary font-[510]'
                  : 'text-text-quaternary hover:text-text-tertiary hover:bg-[rgba(255,245,235,0.04)]'
                }
              `}
            >
              <Icon className="w-3.5 h-3.5 shrink-0" />
              {s.title}
            </button>
          );
        })}
      </nav>

      {/* ── Mobile: horizontal scroll ── */}
      <nav className="flex md:hidden items-center gap-1 overflow-x-auto px-4 py-2 no-scrollbar">
        {sections.map((s) => {
          const isActive = s.id === activeId;
          return (
            <button
              key={s.id}
              onClick={() => onSelect(s.id)}
              className={`
                shrink-0 px-3 py-1 rounded-full
                text-[11px] font-[510] cursor-pointer
                transition-all duration-150
                ${isActive
                  ? 'bg-[rgba(255,245,235,0.12)] text-text-secondary'
                  : 'text-text-quaternary hover:text-text-tertiary'
                }
              `}
            >
              {s.title}
            </button>
          );
        })}
      </nav>
    </>
  );
}

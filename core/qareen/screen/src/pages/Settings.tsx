import { useState, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SETTINGS_SECTIONS, SectionNav } from '@/components/settings';
import { useRegisterPageActions, type PageAction } from '@/hooks/usePageActions';

// ---------------------------------------------------------------------------
// Settings — each nav item shows a different page (not scroll-to-section).
// Left nav on desktop, horizontal pills on mobile.
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialSection = searchParams.get('section') ?? SETTINGS_SECTIONS[0].id;
  const [activeId, setActiveId] = useState(initialSection);

  const selectSection = useCallback(
    (id: string) => {
      setActiveId(id);
      setSearchParams({ section: id }, { replace: true });
    },
    [setSearchParams],
  );

  const activeSection = SETTINGS_SECTIONS.find((s) => s.id === activeId) ?? SETTINGS_SECTIONS[0];
  const ActiveComponent = activeSection.component;

  const pageActions: PageAction[] = useMemo(() => [
    {
      id: 'settings.switch_section',
      label: 'Switch settings section',
      category: 'navigate',
      params: [{ name: 'section', type: 'enum' as const, required: true, description: 'Settings section', options: SETTINGS_SECTIONS.map(s => s.id) }],
      execute: ({ section }) => selectSection(section as string),
    },
  ], [selectSection])
  useRegisterPageActions(pageActions)

  return (
    <div className="h-full overflow-y-auto">
      {/* Mobile nav — horizontal pills, sticky top */}
      <div className="md:hidden sticky top-0 z-40 bg-bg/80 backdrop-blur-md border-b border-border/40">
        <SectionNav
          sections={SETTINGS_SECTIONS}
          activeId={activeId}
          onSelect={selectSection}
        />
      </div>

      {/* Two-column layout */}
      <div className="flex">
        {/* Left: section nav — aligned with hamburger pill (left-3 = 12px) */}
        <div className="hidden md:block w-[172px] shrink-0 sticky top-0 self-start pt-14 pl-3">
          <SectionNav
            sections={SETTINGS_SECTIONS}
            activeId={activeId}
            onSelect={selectSection}
          />
        </div>

        {/* Right: active section content */}
        <div className="flex-1 min-w-0 px-5 md:px-6 pt-4 md:pt-14 pb-10 max-w-[600px]">
          <ActiveComponent />
        </div>
      </div>
    </div>
  );
}

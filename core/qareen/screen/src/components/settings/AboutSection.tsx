import { Info } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { SettingCard, SettingRow, LoadingRows } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// About — AOS version, system identity.
// ---------------------------------------------------------------------------

function useVersion() {
  return useQuery({
    queryKey: ['version'],
    queryFn: async (): Promise<string> => {
      const res = await fetch('/api/version');
      if (!res.ok) return 'unknown';
      const data = await res.json();
      return data.version ?? 'unknown';
    },
    staleTime: 600_000,
  });
}

function AboutContent() {
  const { data: version, isLoading } = useVersion();

  return (
    <SettingCard icon={Info} title="About">
      {isLoading ? (
        <LoadingRows count={2} />
      ) : (
        <>
          <SettingRow label="AOS" value={version ?? 'unknown'} />
          <SettingRow
            label="Interface"
            value="Qareen"
            description="CENTCOM control surface"
          />
        </>
      )}
    </SettingCard>
  );
}

export const aboutSection: SettingsSection = {
  id: 'about',
  title: 'About',
  icon: Info,
  component: AboutContent,
};

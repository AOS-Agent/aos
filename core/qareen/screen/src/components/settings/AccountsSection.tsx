import { Key } from 'lucide-react';
import { useAccounts } from '@/hooks/useConfig';
import { Tag, StatusDot } from '@/components/primitives';
import { SettingCard, SettingRow, LoadingRows } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// Accounts — connected accounts. Shows provider name + enabled status.
// ---------------------------------------------------------------------------

function AccountRow({
  name,
  enabled,
}: {
  name: string;
  enabled: boolean;
}) {
  return (
    <div className="flex items-center justify-between py-3 min-h-[44px]">
      <div className="flex items-center gap-3 min-w-0">
        <StatusDot color={enabled ? 'green' : 'gray'} size="md" />
        <span className="text-[13px] font-[510] text-text-secondary capitalize">
          {name.replace(/_/g, ' ')}
        </span>
      </div>
      <Tag label={enabled ? 'Active' : 'Inactive'} color={enabled ? 'green' : 'gray'} size="sm" />
    </div>
  );
}

function AccountsContent() {
  const { data, isLoading, isError } = useAccounts();

  if (isLoading) {
    return (
      <SettingCard icon={Key} title="Accounts">
        <LoadingRows count={4} />
      </SettingCard>
    );
  }

  if (isError) {
    return (
      <SettingCard icon={Key} title="Accounts">
        <SettingRow
          label="Unavailable"
          description="Account data couldn't be loaded. Qareen may need to be restarted."
        />
      </SettingCard>
    );
  }

  // API returns { accounts: [{name, type, enabled, category}], total }
  const accounts = data?.accounts;
  const entries: { name: string; enabled: boolean }[] = [];

  if (Array.isArray(accounts)) {
    for (const a of accounts as any[]) {
      entries.push({
        name: a.name ?? a.id ?? 'unknown',
        enabled: a.enabled ?? true,
      });
    }
  }

  if (entries.length === 0) {
    return (
      <SettingCard icon={Key} title="Accounts">
        <SettingRow
          label="No accounts linked"
          description="Run onboarding to connect Google, Telegram, or GitHub"
        />
      </SettingCard>
    );
  }

  return (
    <SettingCard icon={Key} title="Accounts">
      {entries.map((entry) => (
        <AccountRow
          key={entry.name}
          name={entry.name}
          enabled={entry.enabled}
        />
      ))}
    </SettingCard>
  );
}

export const accountsSection: SettingsSection = {
  id: 'accounts',
  title: 'Accounts',
  icon: Key,
  component: AccountsContent,
};

import { useState, useCallback } from 'react';
import { Bell } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { SettingCard, Toggle, LoadingRows } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// Notifications — toggle which system messages you receive.
// Preferences stored in operator.yaml under notifications key.
// Cron scripts check these before sending.
// ---------------------------------------------------------------------------

interface NotificationPrefs {
  morning_briefing: boolean;
  evening_checkin: boolean;
  weekly_digest: boolean;
  learning_tips: boolean;
  service_alerts: boolean;
  update_notifications: boolean;
  session_summaries: boolean;
}

const NOTIFICATION_ITEMS: { key: keyof NotificationPrefs; label: string; description: string }[] = [
  { key: 'morning_briefing',     label: 'Morning briefing',      description: 'Daily summary with weather, schedule, and focus tasks' },
  { key: 'evening_checkin',      label: 'Evening check-in',      description: 'End-of-day review and tomorrow preview' },
  { key: 'weekly_digest',        label: 'Weekly digest',          description: 'Sunday evening summary of the week' },
  { key: 'service_alerts',       label: 'Service alerts',         description: 'When a service goes down or needs attention' },
  { key: 'update_notifications', label: 'Update notifications',   description: 'When AOS updates are applied' },
  { key: 'session_summaries',    label: 'Session summaries',      description: 'Recap after long agent sessions' },
  { key: 'learning_tips',        label: 'Learning tips',          description: 'Occasional tips on using AOS features' },
];

function useNotificationPrefs() {
  return useQuery({
    queryKey: ['notification-prefs'],
    queryFn: async (): Promise<NotificationPrefs> => {
      const res = await fetch('/api/config/notifications');
      if (!res.ok) throw new Error('Failed to load notification prefs');
      const data = await res.json();
      return data.notifications;
    },
    staleTime: 300_000,
    refetchOnWindowFocus: true,
  });
}

function useUpdateNotificationPref() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ key, value }: { key: string; value: boolean }) => {
      const res = await fetch('/api/config/notifications', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      });
      if (!res.ok) throw new Error('Failed to update');
      return res.json();
    },
    onSuccess: (data) => {
      qc.setQueryData(['notification-prefs'], data.notifications);
    },
  });
}

function NotificationRow({
  label,
  description,
  checked,
  onToggle,
}: {
  label: string;
  description: string;
  checked: boolean;
  onToggle: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between py-3 min-h-[44px]">
      <div className="flex-1 min-w-0 pr-4">
        <span className="text-[13px] font-[510] text-text-secondary block">{label}</span>
        <span className="text-[11px] text-text-quaternary block mt-0.5">{description}</span>
      </div>
      <Toggle checked={checked} onChange={onToggle} />
    </div>
  );
}

function NotificationsContent() {
  const { data: prefs, isLoading, isError } = useNotificationPrefs();
  const updatePref = useUpdateNotificationPref();

  if (isLoading) {
    return (
      <SettingCard title="Notifications">
        <LoadingRows count={5} />
      </SettingCard>
    );
  }

  if (isError || !prefs) {
    return (
      <SettingCard title="Notifications">
        <div className="py-3">
          <span className="text-[12px] text-text-quaternary">
            Couldn't load notification preferences.
          </span>
        </div>
      </SettingCard>
    );
  }

  return (
    <SettingCard title="Notifications">
      <div className="divide-y divide-border">
        {NOTIFICATION_ITEMS.map((item) => (
          <NotificationRow
            key={item.key}
            label={item.label}
            description={item.description}
            checked={prefs[item.key] ?? true}
            onToggle={(v) => updatePref.mutate({ key: item.key, value: v })}
          />
        ))}
      </div>
    </SettingCard>
  );
}

export const notificationsSection: SettingsSection = {
  id: 'notifications',
  title: 'Notifications',
  icon: Bell,
  component: NotificationsContent,
};

import { useState, useCallback } from 'react';
import { User } from 'lucide-react';
import { useOperator, useUpdateOperator } from '@/hooks/useConfig';
import { Input } from '@/components/primitives';
import { SettingCard, SettingRow, LoadingRows } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// Profile — operator identity. Shows what the system knows about you.
// Editable fields match what the backend actually stores in operator.yaml.
// ---------------------------------------------------------------------------

function ProfileContent() {
  const { data: op, isLoading } = useOperator();
  const updateOp = useUpdateOperator();
  const [editing, setEditing] = useState(false);

  const [draft, setDraft] = useState({
    name: '',
    timezone: '',
    language: '',
    role: '',
  });

  const startEdit = useCallback(() => {
    if (!op) return;
    setDraft({
      name: op.name ?? '',
      timezone: op.timezone ?? '',
      language: op.language ?? 'en',
      role: op.role ?? '',
    });
    setEditing(true);
  }, [op]);

  const cancelEdit = useCallback(() => setEditing(false), []);

  const save = useCallback(() => {
    updateOp.mutate(
      {
        name: draft.name || undefined,
        timezone: draft.timezone || undefined,
        language: draft.language || undefined,
        role: draft.role || undefined,
      },
      { onSuccess: () => setEditing(false) },
    );
  }, [draft, updateOp]);

  const setField = useCallback(
    (field: keyof typeof draft, value: string) =>
      setDraft((prev) => ({ ...prev, [field]: value })),
    [],
  );

  if (isLoading) {
    return (
      <SettingCard icon={User} title="Profile">
        <LoadingRows count={4} />
      </SettingCard>
    );
  }

  const location = op?.location;
  const city = location?.city ?? location?.name;
  const coords =
    location?.latitude && location?.longitude
      ? `${Number(location.latitude).toFixed(2)}, ${Number(location.longitude).toFixed(2)}`
      : null;

  const editButton = !editing ? (
    <button
      onClick={startEdit}
      className="text-[11px] font-[510] text-accent hover:text-accent-hover cursor-pointer transition-colors duration-150"
    >
      Edit
    </button>
  ) : undefined;

  return (
    <SettingCard icon={User} title="Profile" action={editButton}>
      {editing ? (
        <div className="py-3 space-y-3">
          <Input
            label="Name"
            value={draft.name}
            onChange={(e) => setField('name', e.target.value)}
          />
          <Input
            label="Timezone"
            value={draft.timezone}
            onChange={(e) => setField('timezone', e.target.value)}
            placeholder="e.g. America/Toronto"
          />
          <Input
            label="Language"
            value={draft.language}
            onChange={(e) => setField('language', e.target.value)}
            placeholder="e.g. en, ar"
          />
          <Input
            label="Role"
            value={draft.role}
            onChange={(e) => setField('role', e.target.value)}
            placeholder="e.g. Founder, Engineer"
          />
          <div className="flex gap-2 pt-1">
            <button
              onClick={save}
              disabled={updateOp.isPending}
              className="
                px-3 py-1.5 rounded-[5px] text-[12px] font-[510]
                bg-accent text-white hover:bg-accent-hover
                transition-colors duration-150 cursor-pointer
                disabled:opacity-40
              "
            >
              {updateOp.isPending ? 'Saving...' : 'Save'}
            </button>
            <button
              onClick={cancelEdit}
              className="
                px-3 py-1.5 rounded-[5px] text-[12px] font-[510]
                text-text-tertiary hover:text-text-secondary hover:bg-hover
                transition-colors duration-150 cursor-pointer
              "
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <SettingRow label="Name" value={op?.name} />
          {city && (
            <SettingRow
              label="Location"
              value={city}
              description={coords ?? undefined}
            />
          )}
          <SettingRow
            label="Timezone"
            value={op?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone}
          />
          <SettingRow label="Language" value={op?.language ?? 'en'} />
          {op?.role && <SettingRow label="Role" value={op.role} />}
          <SettingRow
            label="Default agent"
            value={op?.agent_name ?? 'chief'}
            description="Which agent handles your requests"
          />
        </>
      )}
    </SettingCard>
  );
}

export const profileSection: SettingsSection = {
  id: 'profile',
  title: 'Profile',
  icon: User,
  component: ProfileContent,
};

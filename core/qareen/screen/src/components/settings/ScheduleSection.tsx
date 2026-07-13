import { useState, useCallback } from 'react';
import { Clock } from 'lucide-react';
import { useOperator, useUpdateOperator } from '@/hooks/useConfig';
import { Input } from '@/components/primitives';
import { SettingCard, SettingRow, LoadingRows } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// Schedule & Rhythm — morning briefing, evening check-in, quiet hours.
// Reads from operator.daily_loop in the YAML.
// ---------------------------------------------------------------------------

function ScheduleContent() {
  const { data: op, isLoading } = useOperator();
  const updateOp = useUpdateOperator();
  const [editing, setEditing] = useState(false);

  const [draft, setDraft] = useState({
    morning_briefing: '',
    evening_checkin: '',
    quiet_hours_start: '',
    quiet_hours_end: '',
  });

  const startEdit = useCallback(() => {
    if (!op) return;
    setDraft({
      morning_briefing: op.morning_briefing ?? '06:00',
      evening_checkin: op.evening_checkin ?? '21:00',
      quiet_hours_start: op.quiet_hours_start ?? '23:00',
      quiet_hours_end: op.quiet_hours_end ?? '06:00',
    });
    setEditing(true);
  }, [op]);

  const cancelEdit = useCallback(() => setEditing(false), []);

  const save = useCallback(() => {
    updateOp.mutate(
      {
        morning_briefing: draft.morning_briefing || undefined,
        evening_checkin: draft.evening_checkin || undefined,
        quiet_hours_start: draft.quiet_hours_start || undefined,
        quiet_hours_end: draft.quiet_hours_end || undefined,
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
      <SettingCard icon={Clock} title="Schedule">
        <LoadingRows count={3} />
      </SettingCard>
    );
  }

  const editButton = !editing ? (
    <button
      onClick={startEdit}
      className="text-[11px] font-[510] text-accent hover:text-accent-hover cursor-pointer transition-colors duration-150"
    >
      Edit
    </button>
  ) : undefined;

  return (
    <SettingCard icon={Clock} title="Schedule" action={editButton}>
      {editing ? (
        <div className="py-3 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Morning briefing"
              value={draft.morning_briefing}
              onChange={(e) => setField('morning_briefing', e.target.value)}
              placeholder="06:00"
            />
            <Input
              label="Evening check-in"
              value={draft.evening_checkin}
              onChange={(e) => setField('evening_checkin', e.target.value)}
              placeholder="21:00"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Quiet hours start"
              value={draft.quiet_hours_start}
              onChange={(e) => setField('quiet_hours_start', e.target.value)}
              placeholder="23:00"
            />
            <Input
              label="Quiet hours end"
              value={draft.quiet_hours_end}
              onChange={(e) => setField('quiet_hours_end', e.target.value)}
              placeholder="06:00"
            />
          </div>
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
          <SettingRow
            label="Morning briefing"
            value={op?.morning_briefing ?? '06:00'}
          />
          <SettingRow
            label="Evening check-in"
            value={op?.evening_checkin ?? '21:00'}
          />
          <SettingRow
            label="Quiet hours"
            value={`${op?.quiet_hours_start ?? '23:00'} \u2013 ${op?.quiet_hours_end ?? '06:00'}`}
            description="Agents won't send notifications during this window"
          />
        </>
      )}
    </SettingCard>
  );
}

export const scheduleSection: SettingsSection = {
  id: 'schedule',
  title: 'Schedule',
  icon: Clock,
  component: ScheduleContent,
};

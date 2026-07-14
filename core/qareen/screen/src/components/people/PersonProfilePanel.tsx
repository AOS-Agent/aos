import { Activity, MessageCircle, Phone, Mail, Camera, AtSign, Clock, Gauge, Users2, Sparkles } from 'lucide-react';
import { usePersonProfile, usePersonClassification } from '@/hooks/usePeople';
import { SectionHeader } from '@/components/primitives/SectionHeader';
import { Tag, type TagColor } from '@/components/primitives/Tag';
import { EmptyState } from '@/components/primitives/EmptyState';
import { SkeletonRows } from '@/components/primitives/Skeleton';

// ---------------------------------------------------------------------------
// PersonProfilePanel — compiled profile + current classification
// ---------------------------------------------------------------------------

interface PersonProfilePanelProps {
  personId: string;
}

const TIER_COLOR: Record<string, TagColor> = {
  core: 'red',
  active: 'green',
  channel_specific: 'teal',
  emerging: 'blue',
  fading: 'orange',
  dormant: 'gray',
  unknown: 'gray',
};

const CHANNEL_COLOR: Record<string, TagColor> = {
  whatsapp: 'green', telegram: 'blue', email: 'purple', slack: 'teal',
  sms: 'orange', phone: 'yellow', imessage: 'blue', signal: 'blue',
};

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('en', { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return iso;
  }
}

function SignalRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: number | string }) {
  return (
    <div className="flex items-center gap-3 py-1.5 px-3 rounded-[5px] bg-bg-secondary/50 border border-border">
      <span className="text-text-quaternary [&>svg]:w-3.5 [&>svg]:h-3.5">{icon}</span>
      <span className="text-[12px] text-text-tertiary flex-1">{label}</span>
      <span className="text-[12px] font-[510] text-text-secondary tabular-nums">{value}</span>
    </div>
  );
}

export function PersonProfilePanel({ personId }: PersonProfilePanelProps) {
  const { data: profile, isLoading: profileLoading } = usePersonProfile(personId);
  const { data: classification, isLoading: classLoading } = usePersonClassification(personId);

  if (profileLoading || classLoading) {
    return <SkeletonRows count={5} />;
  }

  if (!profile) {
    return (
      <EmptyState
        icon={<Sparkles />}
        title="No signals yet"
        description="Run intel extraction to compile this person's profile."
      />
    );
  }

  const p = profile as any;
  const channels: string[] = p.channels_active || [];
  const circles: Array<{ circle_id?: string; name?: string; role?: string }> = p.circles || [];
  const cls = classification as any;
  const tier = cls?.tier ? String(cls.tier).toLowerCase() : null;
  const tags: Array<{ tag: string; confidence: number }> = cls?.context_tags || [];

  return (
    <div className="space-y-5">
      {/* Current classification */}
      <div>
        <SectionHeader label="Classification" icon={<Sparkles />} />
        {tier ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <Tag label={tier} color={TIER_COLOR[tier] || 'gray'} size="md" />
              {cls?.model && (
                <span className="text-[10px] text-text-quaternary">via {cls.model}</span>
              )}
            </div>
            {tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {tags.map((t, i) => (
                  <Tag
                    key={`${t.tag}-${i}`}
                    label={`${t.tag.replace(/_/g, ' ')} ${Math.round((t.confidence ?? 0) * 100)}%`}
                    color="purple"
                    size="sm"
                  />
                ))}
              </div>
            )}
            {cls?.reasoning && (
              <p className="text-[11px] text-text-quaternary leading-[1.6] italic">
                {cls.reasoning}
              </p>
            )}
          </div>
        ) : (
          <p className="text-[11px] text-text-quaternary">Not yet classified.</p>
        )}
      </div>

      {/* Signals */}
      <div>
        <SectionHeader label="Signals" icon={<Activity />} />
        <div className="space-y-1">
          <SignalRow icon={<MessageCircle />} label="Messages" value={p.total_messages ?? 0} />
          <SignalRow icon={<Phone />} label="Calls" value={p.total_calls ?? 0} />
          <SignalRow icon={<Mail />} label="Emails" value={p.total_emails ?? 0} />
          <SignalRow icon={<Camera />} label="Photos" value={p.total_photos ?? 0} />
          <SignalRow icon={<AtSign />} label="Mentions" value={p.total_mentions ?? 0} />
        </div>
      </div>

      {/* Channels */}
      {channels.length > 0 && (
        <div>
          <SectionHeader label="Channels" count={channels.length} />
          <div className="flex flex-wrap gap-1.5">
            {channels.map(ch => (
              <Tag key={ch} label={ch} color={CHANNEL_COLOR[ch.toLowerCase()] || 'gray'} size="sm" />
            ))}
          </div>
        </div>
      )}

      {/* Temporal */}
      <div>
        <SectionHeader label="Temporal" icon={<Clock />} />
        <div className="space-y-1">
          <SignalRow icon={<Clock />} label="First seen" value={fmtDate(p.first_interaction_date)} />
          <SignalRow icon={<Clock />} label="Last seen" value={fmtDate(p.last_interaction_date)} />
          <SignalRow
            icon={<Clock />}
            label="Days since last"
            value={p.days_since_last != null ? `${p.days_since_last}d` : '—'}
          />
          <SignalRow
            icon={<Clock />}
            label="Span"
            value={p.span_years ? `${(p.span_years as number).toFixed(1)}y` : '—'}
          />
          <SignalRow icon={<Clock />} label="Pattern" value={p.dominant_pattern || 'none'} />
        </div>
      </div>

      {/* Density */}
      <div>
        <SectionHeader label="Density" icon={<Gauge />} />
        <div className="flex items-center gap-2">
          <Tag
            label={p.density_rank || 'minimal'}
            color={
              p.density_rank === 'high' ? 'green'
                : p.density_rank === 'medium' ? 'blue'
                : p.density_rank === 'low' ? 'yellow'
                : 'gray'
            }
            size="sm"
          />
          <span className="text-[11px] text-text-quaternary tabular-nums">
            score {((p.density_score ?? 0) as number).toFixed(2)}
          </span>
        </div>
      </div>

      {/* Circles */}
      {circles.length > 0 && (
        <div>
          <SectionHeader label="Circles" icon={<Users2 />} count={circles.length} />
          <div className="flex flex-wrap gap-1.5">
            {circles.map((c, i) => (
              <Tag
                key={`${c.circle_id || c.name || i}`}
                label={c.name || c.circle_id || 'circle'}
                color="teal"
                size="sm"
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default PersonProfilePanel;

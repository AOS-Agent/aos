import { useState, useMemo } from 'react';
import { Loader2, Check, Sparkles } from 'lucide-react';
import { useCorrectClassification } from '@/hooks/usePeople';

// ---------------------------------------------------------------------------
// ClassificationCorrector — tier dropdown + tag multi-select + notes
// Mirrors the HygienePanel correction UX pattern.
// ---------------------------------------------------------------------------

interface ClassificationCorrectorProps {
  personId: string;
  currentTier: string;
  currentTags: Array<{ tag: string; confidence: number }>;
  onSaved?: () => void;
}

const TIER_OPTIONS = [
  'core',
  'active',
  'channel_specific',
  'emerging',
  'fading',
  'dormant',
  'unknown',
] as const;

// Hardcoded from core/engine/people/intel/taxonomy.py CONTEXT_TAGS,
// grouped for display.
const TAG_GROUPS: Array<{ label: string; tags: string[] }> = [
  { label: 'Family', tags: ['family_nuclear', 'family_extended', 'family_inlaw', 'family_chosen'] },
  { label: 'Friends', tags: ['close_friend', 'friend', 'childhood'] },
  { label: 'Work', tags: ['colleague', 'ex_colleague', 'direct_report', 'manager'] },
  { label: 'Business', tags: ['client', 'vendor', 'service_provider', 'business_contact', 'investor', 'cofounder'] },
  { label: 'Community', tags: ['neighbor', 'community_religious', 'community_professional', 'community_hobby', 'community_civic'] },
  { label: 'Mentoring', tags: ['mentor', 'mentee', 'peer_mentor'] },
  { label: 'Stage', tags: ['emerging', 'active', 'faded', 'dormant'] },
  { label: 'Light', tags: ['acquaintance', 'transactional', 'passing'] },
];

export function ClassificationCorrector({
  personId,
  currentTier,
  currentTags,
  onSaved,
}: ClassificationCorrectorProps) {
  const [tier, setTier] = useState<string>(currentTier || 'unknown');
  const [tags, setTags] = useState<Set<string>>(
    () => new Set((currentTags || []).map(t => t.tag))
  );
  const [notes, setNotes] = useState('');
  const [saved, setSaved] = useState(false);
  const correct = useCorrectClassification();

  const changed = useMemo(() => {
    if (tier !== (currentTier || 'unknown')) return true;
    const existing = new Set((currentTags || []).map(t => t.tag));
    if (existing.size !== tags.size) return true;
    for (const t of tags) if (!existing.has(t)) return true;
    return false;
  }, [tier, tags, currentTier, currentTags]);

  const toggleTag = (t: string) => {
    setTags(prev => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  const handleSave = () => {
    const body = {
      person_id: personId,
      tier,
      context_tags: Array.from(tags).map(t => ({ tag: t, confidence: 1.0 })),
      notes: notes.trim() || undefined,
    };
    correct.mutate(body, {
      onSuccess: () => {
        setSaved(true);
        setTimeout(() => setSaved(false), 1800);
        onSaved?.();
      },
    });
  };

  return (
    <div className="space-y-4 p-4 rounded-[7px] bg-bg-secondary/50 border border-border">
      <div className="flex items-center gap-2">
        <Sparkles className="w-3.5 h-3.5 text-accent" />
        <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
          Correct classification
        </span>
      </div>

      {/* Tier dropdown */}
      <div>
        <label className="text-[11px] text-text-tertiary block mb-1.5">Tier</label>
        <select
          value={tier}
          onChange={(e) => setTier(e.target.value)}
          className="w-full h-8 px-2.5 bg-bg-secondary border border-border rounded-[5px] text-[12px] text-text-secondary focus:border-accent/40 focus:outline-none transition-colors cursor-pointer"
          style={{ transitionDuration: 'var(--duration-fast)' }}
        >
          {TIER_OPTIONS.map(t => (
            <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
          ))}
        </select>
      </div>

      {/* Tag multi-select */}
      <div>
        <label className="text-[11px] text-text-tertiary block mb-1.5">
          Context tags
          {tags.size > 0 && (
            <span className="ml-1.5 text-text-quaternary tabular-nums">({tags.size})</span>
          )}
        </label>
        <div className="space-y-2 max-h-[240px] overflow-y-auto pr-1">
          {TAG_GROUPS.map(group => (
            <div key={group.label}>
              <div className="text-[9px] uppercase tracking-[0.06em] text-text-quaternary mb-1">
                {group.label}
              </div>
              <div className="flex flex-wrap gap-1">
                {group.tags.map(t => {
                  const active = tags.has(t);
                  return (
                    <button
                      key={t}
                      onClick={() => toggleTag(t)}
                      className={`
                        text-[10px] px-2 h-5 rounded-xs font-medium leading-[1.2]
                        transition-colors cursor-pointer
                        ${active
                          ? 'bg-accent-subtle text-accent ring-1 ring-accent/40'
                          : 'bg-bg-tertiary text-text-quaternary hover:text-text-tertiary'}
                      `}
                      style={{ transitionDuration: 'var(--duration-instant)' }}
                    >
                      {t.replace(/_/g, ' ')}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Notes */}
      <div>
        <label className="text-[11px] text-text-tertiary block mb-1.5">Notes (optional)</label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Why this correction?"
          rows={2}
          className="w-full px-2.5 py-2 bg-bg-secondary border border-border rounded-[5px] text-[12px] text-text-secondary placeholder:text-text-quaternary focus:border-accent/40 focus:outline-none transition-colors resize-none"
          style={{ transitionDuration: 'var(--duration-fast)' }}
        />
      </div>

      {/* Save button */}
      <div className="flex items-center justify-end gap-2">
        {correct.isError && (
          <span className="text-[10px] text-orange mr-auto">Save failed.</span>
        )}
        <button
          onClick={handleSave}
          disabled={!changed || correct.isPending}
          className="flex items-center gap-1.5 h-8 px-3.5 rounded-full bg-accent text-bg text-[11px] font-[510] hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          {correct.isPending ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : saved ? (
            <Check className="w-3 h-3" />
          ) : null}
          {saved ? 'Saved' : 'Save correction'}
        </button>
      </div>
    </div>
  );
}

export default ClassificationCorrector;

/**
 * ShipmentsEval — hand-labeling queue for detection-eval candidates.
 *
 * Each candidate shows what the pipeline found (number, carrier, layer,
 * confidence, predicted action) plus the source message snippet, and the
 * operator labels it correct / incorrect / missed. Labels feed the
 * confidence-threshold tuning gate (~200 candidates).
 */

import { ClipboardCheck, ThumbsUp, ThumbsDown, EyeOff } from 'lucide-react';
import {
  useEvalQueue, useLabelEvalCandidate, type EvalCandidate,
} from '@/hooks/useShipments';
import { EmptyState, Tag, SkeletonRows, ErrorBanner, Button } from '@/components/primitives';

function sourceSnippet(ev: EvalCandidate): string | null {
  const src = ev.candidate.source;
  if (!src) return null;
  const sender = typeof src.sender === 'string' ? src.sender : '';
  const subject = typeof src.subject === 'string' ? src.subject : '';
  const parts = [sender, subject].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : null;
}

function EvalRow({ ev }: { ev: EvalCandidate }) {
  const label = useLabelEvalCandidate();
  const c = ev.candidate;
  const snippet = sourceSnippet(ev);

  const vote = (l: 'correct' | 'incorrect' | 'missed') =>
    label.mutate({ id: ev.id, label: l });

  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-[7px] bg-bg-secondary border border-border-secondary">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13px] font-mono text-text truncate">{c.tracking_number}</span>
          <Tag label={c.carrier} color="blue" />
          <Tag label={c.layer} color="gray" />
          {ev.predicted && <Tag label={`predicted: ${ev.predicted}`} color="yellow" />}
        </div>
        <div className="text-[11px] text-text-quaternary mt-1 truncate">
          {Math.round((c.confidence ?? 0) * 100)}% confidence
          {snippet ? ` · ${snippet}` : ''}
        </div>
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <Button
          size="sm" variant="secondary" icon={<ThumbsUp />}
          disabled={label.isPending}
          onClick={() => vote('correct')}
          title="Correct detection"
        >
          Correct
        </Button>
        <Button
          size="sm" variant="ghost" icon={<ThumbsDown />}
          disabled={label.isPending}
          onClick={() => vote('incorrect')}
          title="Incorrect — not a real shipment"
        >
          Wrong
        </Button>
        <Button
          size="sm" variant="ghost" icon={<EyeOff />}
          disabled={label.isPending}
          onClick={() => vote('missed')}
          title="Real shipment the pipeline missed"
        >
          Missed
        </Button>
      </div>
    </div>
  );
}

export default function ShipmentsEval() {
  const { data, isLoading, isError } = useEvalQueue();
  const queue = data ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[880px] mx-auto px-6 py-8">
        <h1 className="text-[22px] font-[600] text-text mb-1">Detection eval</h1>
        <p className="text-[13px] text-text-quaternary mb-6">
          Label detection candidates to tune the auto-add confidence threshold.
          {queue.length > 0 && (
            <span className="font-mono"> {queue.length} remaining.</span>
          )}
        </p>

        {isError && <ErrorBanner />}

        {isLoading ? (
          <SkeletonRows count={6} />
        ) : queue.length === 0 ? (
          <EmptyState
            icon={<ClipboardCheck />}
            title="Queue is empty"
            description="All eval candidates are labeled. New ones appear as detection runs on incoming mail."
          />
        ) : (
          <div className="space-y-2">
            {queue.map((ev) => <EvalRow key={ev.id} ev={ev} />)}
          </div>
        )}
      </div>
    </div>
  );
}

import { useState } from 'react';
import { Check, X as XIcon, Loader2, Send, UserRound } from 'lucide-react';
import { Tag } from '@/components/primitives';
import type { ConverseAction } from '@/lib/converseApi';
import { timeAgo } from '@/lib/converseApi';
import { useApproveAction, useRejectAction } from '@/hooks/useConverse';

interface Props {
  action: ConverseAction;
  /** Hide the session id chip when already rendered inside that session's detail. */
  contextLabel?: string;
}

export function ActionApprovalCard({ action, contextLabel }: Props) {
  const approve = useApproveAction();
  const reject = useRejectAction();
  const [confirmReject, setConfirmReject] = useState(false);
  const busy = approve.isPending || reject.isPending;

  const isSendReply = action.kind === 'send_reply';
  const isTouchpoint = action.kind === 'human_touchpoint';

  function handleReject() {
    if (!confirmReject) {
      setConfirmReject(true);
      setTimeout(() => setConfirmReject(false), 3000);
      return;
    }
    reject.mutate(action.id);
  }

  return (
    <div className="rounded-[10px] bg-bg-secondary border border-yellow/25 relative overflow-hidden">
      <div className="absolute left-0 top-3 bottom-3 w-[3px] rounded-r-full bg-yellow/70" />
      <div className="px-6 py-5 pl-7">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            {isSendReply ? (
              <Send className="w-3.5 h-3.5 text-yellow" />
            ) : (
              <UserRound className="w-3.5 h-3.5 text-yellow" />
            )}
            <span className="text-[11px] font-[590] uppercase tracking-[0.1em] text-yellow">
              {isSendReply ? 'Reply held for approval' : 'Needs a human touchpoint'}
            </span>
          </div>
          <span className="text-[10px] text-text-quaternary font-mono shrink-0">
            {timeAgo(action.created_at)}
          </span>
        </div>

        {contextLabel && (
          <div className="mb-2">
            <Tag label={contextLabel} color="gray" />
          </div>
        )}

        {isSendReply && action.payload.text && (
          <p className="font-serif text-[15px] text-text italic leading-[1.5] bg-bg-tertiary/50 rounded-[7px] px-4 py-3">
            “{action.payload.text}”
          </p>
        )}

        {isTouchpoint && (
          <div className="space-y-2">
            {action.payload.description && (
              <p className="text-[13.5px] text-text-secondary leading-[1.55]">
                {action.payload.description}
              </p>
            )}
            {action.payload.artifact_url && (
              <a
                href={action.payload.artifact_url}
                target="_blank"
                rel="noreferrer"
                className="text-[12px] text-accent hover:text-accent-hover break-all underline underline-offset-2"
              >
                {action.payload.artifact_url}
              </a>
            )}
          </div>
        )}

        {action.gate_reasons.length > 0 && (
          <div className="mt-3">
            <div className="text-[10px] font-[590] uppercase tracking-[0.1em] text-text-quaternary mb-1.5">
              Held back because
            </div>
            <ul className="space-y-1">
              {action.gate_reasons.map((r, i) => (
                <li key={i} className="text-[12px] text-text-tertiary leading-[1.5] flex gap-2">
                  <span className="text-yellow/60 shrink-0 mt-[3px]">•</span>
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 pt-4 border-t border-border flex items-center justify-end gap-2">
          <button
            onClick={handleReject}
            disabled={busy}
            className={`h-8 px-3 rounded-[6px] flex items-center gap-1.5 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${
              confirmReject
                ? 'bg-red-muted border border-red/30 text-red'
                : 'text-text-tertiary hover:text-red hover:bg-red-muted border border-transparent hover:border-red/20'
            }`}
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            {reject.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <XIcon className="w-3.5 h-3.5" />
            )}
            <span className="text-[11px] font-[560]">{confirmReject ? 'Confirm' : 'Reject'}</span>
          </button>
          <button
            onClick={() => approve.mutate(action.id)}
            disabled={busy}
            className="h-8 px-3 rounded-[6px] flex items-center gap-1.5 bg-green-muted border border-green/30 text-green hover:bg-green/15 hover:border-green/50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            {approve.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Check className="w-3.5 h-3.5" />
            )}
            <span className="text-[11px] font-[560]">Approve</span>
          </button>
        </div>
      </div>
    </div>
  );
}

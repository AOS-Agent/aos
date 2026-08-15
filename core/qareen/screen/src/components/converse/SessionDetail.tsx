import { useEffect, useMemo, useRef } from 'react';
import {
  ArrowDownLeft,
  ArrowUpRight,
  Clock,
  AlertTriangle,
  ExternalLink,
  Loader2,
} from 'lucide-react';
import { EmptyState, ErrorBanner, StatusDot, Tag } from '@/components/primitives';
import { useLoadingTimeout } from '@/hooks/useLoadingTimeout';
import { useConverseSession } from '@/hooks/useConverse';
import { SessionControls } from './SessionControls';
import { ActionApprovalCard } from './ActionApprovalCard';
import type { ConverseMessage } from '@/lib/converseApi';
import {
  channelLabel,
  displayName,
  formatMessageTime,
  formatTimestamp,
  modeColor,
  modeLabel,
  statusColor,
  statusLabel,
} from '@/lib/converseApi';

interface Props {
  sessionId: string;
}

export function SessionDetail({ sessionId }: Props) {
  const { data, isLoading, isError, refetch } = useConverseSession(sessionId);
  const timedOut = useLoadingTimeout(isLoading);
  const scrollRef = useRef<HTMLDivElement>(null);

  const messages = useMemo(() => data?.messages ?? [], [data?.messages]);

  // Newest last for a chat read order — the API returns most-recent-first.
  const ordered = useMemo(() => [...messages].reverse(), [messages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [ordered.length]);

  if (isLoading && !timedOut) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="w-5 h-5 text-text-quaternary animate-spin" />
      </div>
    );
  }

  if ((isError || timedOut) && !data) {
    return (
      <div className="h-full flex flex-col items-center justify-center px-6">
        <ErrorBanner message="Couldn't load this session." />
        <button
          type="button"
          onClick={() => refetch()}
          className="text-[12px] text-text-tertiary hover:text-text-secondary cursor-pointer"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!data) return null;

  const handling = data.status === 'handling';

  return (
    <div className="h-full flex min-w-0">
      {/* Transcript */}
      <div className="flex-1 min-w-0 flex flex-col border-r border-border">
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
          {ordered.length === 0 ? (
            <EmptyState
              icon={<Clock />}
              title="No messages yet"
              description="The transcript fills in as the conversation happens."
            />
          ) : (
            <div className="flex flex-col gap-3">
              {ordered.map((m) => (
                <MessageBubble key={m.id} message={m} />
              ))}
              {handling && <TypingIndicator />}
            </div>
          )}
        </div>
        <div className="px-6 py-4 border-t border-border">
          <SessionControls session={data} />
        </div>
      </div>

      {/* Side panel */}
      <div className="w-[320px] shrink-0 overflow-y-auto px-5 py-6">
        <div className="mb-5">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <Tag label={modeLabel(data.mode)} color={modeColor(data.mode)} />
            <Tag label={channelLabel(data.channel)} color="gray" />
            <StatusDot color={statusColor(data.status)} label={statusLabel(data.status)} pulse={handling} />
          </div>
          <h2 className="text-[18px] font-[650] text-text tracking-[-0.01em]">{displayName(data)}</h2>
          <p className="text-[11px] text-text-quaternary font-mono mt-0.5">{data.counterpart_handle}</p>
        </div>

        <Section title="Mission">
          <p className="font-serif text-[14px] text-text-secondary leading-[1.55] italic">{data.mission}</p>
        </Section>

        {data.success_criteria && (
          <Section title="Success criteria">
            <p className="text-[12.5px] text-text-tertiary leading-[1.5]">{data.success_criteria}</p>
          </Section>
        )}

        {data.constraints && (
          <Section title="Constraints">
            <p className="text-[12.5px] text-text-tertiary leading-[1.5]">{data.constraints}</p>
          </Section>
        )}

        {data.state_summary && (
          <Section title="Working state">
            <div className="bg-bg-tertiary/50 rounded-[7px] px-3 py-2.5 whitespace-pre-wrap text-[12px] text-text-secondary leading-[1.55] font-serif">
              {data.state_summary}
            </div>
          </Section>
        )}

        {data.artifacts.length > 0 && (
          <Section title="Artifacts">
            <div className="space-y-1.5">
              {data.artifacts.map((a, i) => (
                <a
                  key={i}
                  href={a.url as string | undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 text-[12px] text-accent hover:text-accent-hover break-all"
                >
                  <ExternalLink className="w-3 h-3 shrink-0" />
                  {(a.note as string | undefined) ?? (a.kind as string)}
                </a>
              ))}
            </div>
          </Section>
        )}

        <Section title="Counters">
          <div className="grid grid-cols-2 gap-y-2 text-[12px]">
            <CounterCell label="Turns" value={data.turn_count} />
            <CounterCell label="Sent" value={`${data.sent_count}/${data.max_messages}`} />
            <CounterCell label="Errors" value={data.error_count} tone={data.error_count > 0 ? 'red' : undefined} />
            <CounterCell label="Trust" value={`L${data.trust_level}`} />
          </div>
          <p className="text-[10px] text-text-quaternary mt-2">
            Trust level is set at creation — not editable from this session.
          </p>
        </Section>

        <Section title="Timeline">
          <div className="space-y-1.5 text-[11.5px] text-text-tertiary">
            <div className="flex justify-between">
              <span>Created</span>
              <span className="font-mono text-text-quaternary">{formatTimestamp(data.created_at)}</span>
            </div>
            <div className="flex justify-between">
              <span>Updated</span>
              <span className="font-mono text-text-quaternary">{formatTimestamp(data.updated_at)}</span>
            </div>
            {data.expires_at && (
              <div className="flex justify-between">
                <span>Expires</span>
                <span className="font-mono text-text-quaternary">{formatTimestamp(data.expires_at)}</span>
              </div>
            )}
            {data.closed_at && (
              <div className="flex justify-between">
                <span>Closed</span>
                <span className="font-mono text-text-quaternary">{formatTimestamp(data.closed_at)}</span>
              </div>
            )}
          </div>
        </Section>

        {data.paused_reason && (
          <div className="mt-4 flex items-start gap-2 text-[12px] text-yellow bg-yellow/10 rounded-[6px] px-3 py-2.5 border border-yellow/20">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>Held: {data.paused_reason}</span>
          </div>
        )}

        {data.pending_actions.length > 0 && (
          <div className="mt-5 space-y-3">
            <div className="text-[10px] font-[590] uppercase tracking-[0.12em] text-text-quaternary">
              Needs approval
            </div>
            {data.pending_actions.map((a) => (
              <ActionApprovalCard key={a.id} action={a} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <div className="text-[10px] font-[590] uppercase tracking-[0.1em] text-text-quaternary mb-1.5">
        {title}
      </div>
      {children}
    </div>
  );
}

function CounterCell({ label, value, tone }: { label: string; value: string | number; tone?: 'red' } ) {
  return (
    <div>
      <div className={`font-mono text-[15px] font-[500] ${tone === 'red' ? 'text-red' : 'text-text'}`}>
        {value}
      </div>
      <div className="text-[10px] text-text-quaternary">{label}</div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-2 pl-1">
      <div className="flex items-center gap-1 bg-bg-tertiary rounded-full px-3 py-2">
        <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary animate-bounce [animation-delay:0ms]" />
        <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary animate-bounce [animation-delay:120ms]" />
        <span className="w-1.5 h-1.5 rounded-full bg-text-quaternary animate-bounce [animation-delay:240ms]" />
      </div>
      <span className="text-[11px] text-text-quaternary">thinking…</span>
    </div>
  );
}

function MessageBubble({ message }: { message: ConverseMessage }) {
  if (message.role === 'system') {
    return (
      <div className="flex justify-center">
        <span className="text-[11px] text-text-quaternary italic">{message.text}</span>
      </div>
    );
  }

  if (message.direction === 'internal') {
    return (
      <div className="flex justify-center">
        <div className="max-w-[80%] rounded-[8px] border border-dashed border-border-tertiary px-4 py-2.5">
          <div className="text-[10px] font-[590] uppercase tracking-[0.08em] text-text-quaternary mb-1">
            Note to agent — the contact never sees this
          </div>
          <p className="text-[12.5px] text-text-tertiary leading-[1.5]">{message.text}</p>
        </div>
      </div>
    );
  }

  const isContact = message.role === 'contact';

  return (
    <div className={`flex ${isContact ? 'justify-start' : 'justify-end'}`}>
      <div className={`flex flex-col max-w-[75%] ${isContact ? 'items-start' : 'items-end'}`}>
        <div className="flex items-center gap-1.5 mb-1 px-1">
          {isContact ? (
            <ArrowDownLeft className="w-3 h-3 text-blue" />
          ) : (
            <ArrowUpRight className="w-3 h-3 text-accent" />
          )}
          <span className="text-[10px] text-text-quaternary">
            {isContact ? 'Contact' : message.role === 'operator' ? 'You' : 'Agent'} ·{' '}
            {formatMessageTime(message.ts)}
          </span>
        </div>
        <div
          className={`rounded-[10px] px-4 py-2.5 font-serif text-[14px] leading-[1.5] ${
            isContact ? 'bg-bg-tertiary text-text-secondary' : 'bg-accent-subtle text-text'
          }`}
        >
          {message.text}
        </div>
        <MessageStateChip state={message.state} error={message.error} />
      </div>
    </div>
  );
}

function MessageStateChip({ state, error }: { state: ConverseMessage['state']; error: string | null }) {
  if (state === 'queued') {
    return (
      <span className="flex items-center gap-1 text-[10px] text-text-quaternary mt-1 px-1">
        <Clock className="w-2.5 h-2.5" />
        Queued — waiting for the converse service to send
      </span>
    );
  }
  if (state === 'send_failed' || state === 'failed') {
    return (
      <span className="flex items-center gap-1 text-[10px] text-red mt-1 px-1" title={error ?? undefined}>
        <AlertTriangle className="w-2.5 h-2.5" />
        {state === 'send_failed' ? 'Send failed' : 'Failed'}
      </span>
    );
  }
  return null;
}

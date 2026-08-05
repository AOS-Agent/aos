import { useState } from 'react';
import { Pause, Play, UserCog, LogOut, Square, Loader2, Send, StickyNote } from 'lucide-react';
import type { ConverseSession } from '@/lib/converseApi';
import { isTerminalStatus } from '@/lib/converseApi';
import {
  useCloseSession,
  useInjectMessage,
  usePauseSession,
  useReleaseSession,
  useResumeSession,
  useTakeoverSession,
} from '@/hooks/useConverse';

interface Props {
  session: ConverseSession;
}

const RESUMABLE = new Set(['paused', 'escalated', 'capped']);

export function SessionControls({ session }: Props) {
  const pause = usePauseSession();
  const resume = useResumeSession();
  const takeover = useTakeoverSession();
  const release = useReleaseSession();
  const close = useCloseSession();
  const inject = useInjectMessage();

  const [text, setText] = useState('');
  const [deliverNote, setDeliverNote] = useState(session.status !== 'takeover');
  const [confirmClose, setConfirmClose] = useState(false);

  const terminal = isTerminalStatus(session.status);
  const busy =
    pause.isPending || resume.isPending || takeover.isPending || release.isPending || close.isPending;

  function handleClose() {
    if (!confirmClose) {
      setConfirmClose(true);
      setTimeout(() => setConfirmClose(false), 3000);
      return;
    }
    close.mutate({ id: session.id, reason: 'operator' });
  }

  function submitInject() {
    const trimmed = text.trim();
    if (!trimmed || inject.isPending) return;
    const deliver = session.status === 'takeover' ? 'send' : deliverNote ? 'note' : 'send';
    inject.mutate({ id: session.id, text: trimmed, deliver }, { onSuccess: () => setText('') });
  }

  if (terminal) {
    return (
      <div className="rounded-[10px] bg-bg-secondary border border-border-secondary px-5 py-4">
        <p className="text-[12.5px] text-text-tertiary">
          Closed — <span className="text-text-secondary">{session.close_reason ?? session.status}</span>.
          No further actions on this session.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Status transition buttons */}
      <div className="flex items-center gap-2 flex-wrap">
        {RESUMABLE.has(session.status) && (
          <ControlButton
            icon={<Play className="w-3.5 h-3.5" />}
            label="Resume"
            onClick={() => resume.mutate(session.id)}
            loading={resume.isPending}
            disabled={busy}
            variant="accent"
          />
        )}
        {!RESUMABLE.has(session.status) && session.status !== 'takeover' && (
          <ControlButton
            icon={<Pause className="w-3.5 h-3.5" />}
            label="Pause"
            onClick={() => pause.mutate({ id: session.id })}
            loading={pause.isPending}
            disabled={busy}
          />
        )}
        {session.status === 'takeover' ? (
          <ControlButton
            icon={<LogOut className="w-3.5 h-3.5" />}
            label="Release"
            onClick={() => release.mutate(session.id)}
            loading={release.isPending}
            disabled={busy}
          />
        ) : (
          <ControlButton
            icon={<UserCog className="w-3.5 h-3.5" />}
            label="Take over"
            onClick={() => takeover.mutate(session.id)}
            loading={takeover.isPending}
            disabled={busy}
          />
        )}
        <ControlButton
          icon={<Square className="w-3.5 h-3.5" />}
          label={confirmClose ? 'Confirm close' : 'Close'}
          onClick={handleClose}
          loading={close.isPending}
          disabled={busy}
          variant={confirmClose ? 'danger' : 'default'}
          className="ml-auto"
        />
      </div>

      {/* Inject composer */}
      <div className="rounded-[10px] bg-bg-secondary border border-border-secondary p-3">
        {session.status !== 'takeover' && (
          <div className="flex items-center gap-1 mb-2 bg-bg-tertiary/60 rounded-[7px] p-0.5 w-fit">
            <ToggleButton
              icon={<StickyNote className="w-3 h-3" />}
              label="Note to agent"
              active={deliverNote}
              onClick={() => setDeliverNote(true)}
            />
            <ToggleButton
              icon={<Send className="w-3 h-3" />}
              label="Send as me"
              active={!deliverNote}
              onClick={() => setDeliverNote(false)}
            />
          </div>
        )}
        {session.status === 'takeover' && (
          <div className="flex items-center gap-1.5 mb-2 text-[10px] font-[590] uppercase tracking-[0.08em] text-accent">
            <Send className="w-3 h-3" />
            Sending as you — the contact sees this directly
          </div>
        )}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submitInject();
          }}
          placeholder={
            session.status === 'takeover'
              ? 'Message the contact directly…'
              : deliverNote
                ? 'Guidance for the next turn — the contact never sees this…'
                : 'Send this verbatim, right now…'
          }
          rows={2}
          className="w-full bg-transparent outline-none text-[13px] text-text placeholder:text-text-quaternary resize-none font-serif"
        />
        <div className="flex items-center justify-end gap-2 mt-1">
          <span className="text-[10px] text-text-quaternary mr-auto">⌘⏎ to send</span>
          <button
            type="button"
            onClick={submitInject}
            disabled={!text.trim() || inject.isPending}
            className="h-8 px-3 rounded-[6px] flex items-center gap-1.5 bg-accent text-on-accent hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            {inject.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            <span className="text-[11px] font-[560]">
              {session.status === 'takeover' ? 'Send' : deliverNote ? 'Add note' : 'Send'}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}

function ControlButton({
  icon,
  label,
  onClick,
  loading,
  disabled,
  variant = 'default',
  className = '',
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  loading?: boolean;
  disabled?: boolean;
  variant?: 'default' | 'accent' | 'danger';
  className?: string;
}) {
  const styles =
    variant === 'accent'
      ? 'bg-green-muted border-green/30 text-green hover:bg-green/15 hover:border-green/50'
      : variant === 'danger'
        ? 'bg-red-muted border-red/30 text-red hover:bg-red/15'
        : 'bg-bg-tertiary border-border-secondary hover:border-border-tertiary hover:bg-bg-quaternary text-text-secondary';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`h-9 px-3.5 rounded-[7px] flex items-center gap-2 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed border ${styles} ${className}`}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : icon}
      <span className="text-[12px] font-[560] tracking-[0.005em]">{label}</span>
    </button>
  );
}

function ToggleButton({
  icon,
  label,
  active,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1.5 h-7 px-2.5 rounded-[6px] text-[11px] font-[560] transition-colors cursor-pointer ${
        active ? 'bg-bg-quaternary text-text' : 'text-text-tertiary hover:text-text-secondary'
      }`}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {icon}
      {label}
    </button>
  );
}

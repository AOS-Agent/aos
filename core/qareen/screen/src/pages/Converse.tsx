/**
 * Converse — full interactive control over autonomous conversation sessions.
 *
 * Converse is the unification of Sentinel (mode='sentinel', voice='operator')
 * and Envoy (mode='envoy', voice='agent') onto one supervised runtime that
 * holds goal-directed multi-turn conversations over iMessage and Slack.
 * This screen is the live cockpit: session list on the left, live transcript
 * + controls + approvals on the right. See ~/.aos/tmp/sessions-build/PLAN.md
 * §7 for the full surface spec and core/qareen/api/converse.py (T2c) for the
 * exact API contract this binds to.
 */

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { MessagesSquare, ShieldAlert } from 'lucide-react';
import { EmptyState, ErrorBanner, SkeletonRows } from '@/components/primitives';
import { useLoadingTimeout } from '@/hooks/useLoadingTimeout';
import { useConverseActions, useConverseSessions } from '@/hooks/useConverse';
import { useConverseStream, useConverseConnected } from '@/hooks/useConverseStream';
import { SessionList } from '@/components/converse/SessionList';
import { SessionDetail } from '@/components/converse/SessionDetail';
import { ActionApprovalCard } from '@/components/converse/ActionApprovalCard';
import { displayName, type SessionStatusFilter } from '@/lib/converseApi';

type Tab = 'active' | 'all';

const TABS: { key: Tab; label: string }[] = [
  { key: 'active', label: 'Active' },
  { key: 'all', label: 'All' },
];

export default function Converse() {
  useConverseStream();
  const connected = useConverseConnected();

  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>('active');
  const selectedId = searchParams.get('session');

  const statusFilter: SessionStatusFilter = tab === 'active' ? 'active' : 'all';
  const { data, isLoading, isError } = useConverseSessions({ status: statusFilter, limit: 100 });
  const { data: actionsData } = useConverseActions();
  const timedOut = useLoadingTimeout(isLoading);

  const sessions = useMemo(() => data?.sessions ?? [], [data?.sessions]);
  const pendingActions = useMemo(() => actionsData?.actions ?? [], [actionsData?.actions]);

  const pendingCountBySession = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const a of pendingActions) counts[a.session_id] = (counts[a.session_id] ?? 0) + 1;
    return counts;
  }, [pendingActions]);

  const selectedSession = useMemo(
    () => sessions.find((s) => s.id === selectedId) ?? null,
    [sessions, selectedId],
  );

  // Keep the deep-linked session selectable even once it falls off the
  // "active" filter (e.g. it just closed) — refetch under "all" instead of
  // silently dropping the operator's current view.
  useEffect(() => {
    if (selectedId && !selectedSession && tab === 'active' && !isLoading) {
      setTab('all');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, selectedSession, isLoading]);

  function selectSession(id: string | null) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (id) next.set('session', id);
      else next.delete('session');
      return next;
    });
  }

  return (
    <div className="h-full flex flex-col min-w-0">
      {/* Header */}
      <div className="px-6 pt-8 pb-4 shrink-0">
        <div className="flex items-center gap-3 mb-1">
          <h1 className="text-[22px] font-[600] text-text">Converse</h1>
          <span className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-green' : 'bg-red'}`} />
            <span className="text-[10px] text-text-quaternary uppercase tracking-[0.06em]">
              {connected ? 'Live' : 'Reconnecting'}
            </span>
          </span>
        </div>
        <p className="text-[13px] text-text-quaternary">
          Autonomous conversations Sentinel and Envoy are holding on your behalf.
        </p>
      </div>

      {/* Pending approvals — across every session, surfaced first */}
      {pendingActions.length > 0 && (
        <div className="px-6 pb-4 shrink-0">
          <div className="flex items-center gap-2 mb-3">
            <ShieldAlert className="w-3.5 h-3.5 text-yellow" />
            <span className="text-[11px] font-[590] uppercase tracking-[0.12em] text-text-quaternary">
              Needs approval
            </span>
            <span className="text-[11px] font-mono text-text-quaternary">{pendingActions.length}</span>
          </div>
          <div className="flex gap-3 overflow-x-auto pb-1">
            {pendingActions.map((a) => {
              const s = sessions.find((x) => x.id === a.session_id);
              return (
                <div key={a.id} className="w-[380px] shrink-0">
                  <ActionApprovalCard action={a} contextLabel={s ? displayName(s) : undefined} />
                </div>
              );
            })}
          </div>
        </div>
      )}

      {isError && (
        <div className="px-6 shrink-0">
          <ErrorBanner message="Couldn't reach the converse API." />
        </div>
      )}

      {/* Body — list + detail */}
      <div className="flex-1 min-h-0 flex">
        <div className="w-[380px] shrink-0 border-r border-border flex flex-col min-h-0">
          <div className="px-4 pt-2 pb-3 shrink-0">
            <div className="flex items-center gap-1 bg-bg-secondary border border-border-secondary rounded-[9px] p-1 w-fit">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => setTab(t.key)}
                  className={`text-[11.5px] font-[560] px-3 h-7 rounded-[6px] flex items-center gap-1.5 transition-colors cursor-pointer ${
                    tab === t.key ? 'bg-bg-quaternary text-text' : 'text-text-tertiary hover:text-text-secondary'
                  }`}
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-4 pb-6">
            {isLoading && !timedOut ? (
              <SkeletonRows count={5} />
            ) : (
              <SessionList
                sessions={sessions}
                selectedId={selectedId}
                onSelect={(s) => selectSession(s.id)}
                pendingCountBySession={pendingCountBySession}
                emptyTitle={tab === 'active' ? 'No active sessions' : 'No sessions yet'}
                emptyDescription={
                  tab === 'active'
                    ? 'Sentinel and Envoy sessions appear here the moment they start watching a conversation.'
                    : 'Closed and past sessions will show up here too.'
                }
              />
            )}
          </div>
        </div>

        <div className="flex-1 min-w-0">
          {selectedSession ? (
            <SessionDetail sessionId={selectedSession.id} />
          ) : (
            <div className="h-full flex items-center justify-center">
              <EmptyState
                icon={<MessagesSquare />}
                title="Select a session"
                description="Choose a conversation on the left to see its transcript and controls."
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

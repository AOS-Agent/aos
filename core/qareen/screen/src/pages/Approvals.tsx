import { useState, useEffect, useCallback } from 'react';
import { ShieldCheck, Check, X, AlertTriangle, RefreshCw, Undo2, Inbox } from 'lucide-react';
import { EmptyState, Tag, StatusDot, SkeletonRows } from '@/components/primitives';
import type { Card, CardType } from '@/lib/types';

const API = '/api';

function cardTypeColor(type: CardType): 'blue' | 'green' | 'orange' | 'purple' | 'red' | 'gray' {
  switch (type) {
    case 'task': return 'blue';
    case 'decision': return 'purple';
    case 'vault': return 'green';
    case 'reply': return 'orange';
    case 'system': return 'red';
    case 'suggestion': return 'gray';
    default: return 'gray';
  }
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/* ---------- Undo Toast ---------- */
function UndoToast({ label, onUndo, onExpire }: { label: string; onUndo: () => void; onExpire: () => void }) {
  const [remaining, setRemaining] = useState(5);

  useEffect(() => {
    const interval = setInterval(() => {
      setRemaining(prev => {
        if (prev <= 1) {
          clearInterval(interval);
          onExpire();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [onExpire]);

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-bg-secondary border border-border-secondary rounded-[7px] px-4 py-2.5 shadow-[var(--shadow-medium)]"
      style={{ animation: 'slideUp 220ms var(--ease-out)' }}
    >
      <span className="text-[12px] text-text-secondary">{label}</span>
      <div className="w-5 h-5 rounded-full border-2 border-accent flex items-center justify-center relative">
        <span className="text-[9px] font-mono text-accent font-[600]">{remaining}</span>
        <svg className="absolute inset-0 w-5 h-5 -rotate-90" viewBox="0 0 20 20">
          <circle
            cx="10" cy="10" r="8"
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth="2"
            strokeDasharray={`${2 * Math.PI * 8}`}
            strokeDashoffset={`${2 * Math.PI * 8 * (1 - remaining / 5)}`}
            strokeLinecap="round"
            className="transition-all duration-1000 ease-linear"
          />
        </svg>
      </div>
      <button
        onClick={onUndo}
        className="text-[11px] font-[590] text-accent hover:text-accent-hover flex items-center gap-1 cursor-pointer transition-colors"
      >
        <Undo2 className="w-3 h-3" />
        Undo
      </button>
    </div>
  );
}

/* ---------- Approval Card ---------- */
function ApprovalCard({
  card,
  onApprove,
  onDismiss,
}: {
  card: Card;
  onApprove: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const isPending = card.status === 'pending';

  return (
    <div
      className={`bg-bg-secondary rounded-[7px] border transition-all cursor-default ${
        isPending
          ? 'border-border-secondary hover:border-border-tertiary'
          : 'border-border opacity-60'
      }`}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {/* Pending accent bar */}
      {isPending && (
        <div className="h-[2px] bg-gradient-to-r from-accent/60 via-accent to-accent/60 rounded-t-[7px]" />
      )}

      <div className="p-5">
        {/* Top row: type + time */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Tag label={card.card_type} color={cardTypeColor(card.card_type)} size="sm" />
            <StatusDot
              color={card.status === 'pending' ? 'yellow' : card.status === 'approved' ? 'green' : card.status === 'dismissed' ? 'gray' : 'red'}
              size="sm"
              label={card.status}
            />
          </div>
          <span className="text-[10px] text-text-quaternary">{timeAgo(card.created_at)}</span>
        </div>

        {/* Title + body */}
        <h3 className="text-[14px] font-[560] text-text tracking-[-0.01em] mb-1.5 font-serif">{card.title}</h3>
        <p className="text-[12px] text-text-tertiary leading-[1.6] mb-3 line-clamp-2 font-serif">{card.body}</p>

        {/* Source utterance */}
        {card.source_utterance && (
          <div className="mb-4 border-l-2 border-accent/40 pl-3 py-0.5">
            <p className="text-[11px] text-text-quaternary italic font-serif leading-relaxed">"{card.source_utterance}"</p>
          </div>
        )}

        {/* Confidence + actions */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-14 h-[5px] bg-bg-tertiary rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-accent transition-all duration-500"
                style={{ width: `${card.confidence * 100}%` }}
              />
            </div>
            <span className="text-[9px] font-mono text-text-quaternary">{Math.round(card.confidence * 100)}%</span>
          </div>

          {isPending && (
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => onDismiss(card.id)}
                className="w-8 h-8 flex items-center justify-center rounded-[5px] text-text-quaternary hover:text-red hover:bg-red-muted transition-colors cursor-pointer"
                title="Dismiss"
              >
                <X className="w-4 h-4" />
              </button>
              <button
                onClick={() => onApprove(card.id)}
                className="h-8 px-3 flex items-center justify-center gap-1.5 rounded-[5px] bg-green/10 text-green hover:bg-green/20 transition-colors cursor-pointer"
                title="Approve"
              >
                <Check className="w-3.5 h-3.5" />
                <span className="text-[11px] font-[590]">Approve</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------- Main Page ---------- */
export default function ApprovalsPage() {
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [filter, setFilter] = useState<'all' | 'pending' | 'approved' | 'dismissed'>('all');
  const [undoState, setUndoState] = useState<{ id: string; action: 'approve' | 'dismiss'; prevStatus: Card['status'] } | null>(null);

  const loadCards = useCallback(() => {
    setLoading(true);
    setError(false);
    fetch(`${API}/cards`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(data => { setCards(data.cards || data || []); setLoading(false); })
      .catch(() => { setLoading(false); setError(true); });
  }, []);

  useEffect(() => { loadCards(); }, [loadCards]);

  const handleAction = useCallback(async (id: string, action: 'approve' | 'dismiss') => {
    const card = cards.find(c => c.id === id);
    if (!card) return;

    // Optimistic update
    const prevStatus = card.status;
    const newStatus = action === 'approve' ? 'approved' : 'dismissed';
    setCards(prev => prev.map(c => c.id === id ? { ...c, status: newStatus } as Card : c));
    setUndoState({ id, action, prevStatus });
  }, [cards]);

  const commitAction = useCallback(async (id: string, action: 'approve' | 'dismiss') => {
    try {
      await fetch(`${API}/cards/${id}/${action}`, { method: 'POST' });
    } catch { /* empty */ }
    setUndoState(null);
  }, []);

  const handleUndo = useCallback(() => {
    if (!undoState) return;
    setCards(prev => prev.map(c => c.id === undoState.id ? { ...c, status: undoState.prevStatus } as Card : c));
    setUndoState(null);
  }, [undoState]);

  const handleUndoExpire = useCallback(() => {
    if (!undoState) return;
    commitAction(undoState.id, undoState.action);
  }, [undoState, commitAction]);

  const filtered = filter === 'all' ? cards : cards.filter(c => c.status === filter);
  const pendingCount = cards.filter(c => c.status === 'pending').length;

  return (
    <div className="bg-bg min-h-full">
      <div className="px-6 md:px-10 py-6 md:py-8 max-w-[1200px] mx-auto overflow-y-auto h-full">

        {/* Page header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <div className="flex items-center gap-2.5 mb-1">
              <ShieldCheck className="w-4 h-4 text-text-quaternary" />
              <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">Approval queue</span>
              {pendingCount > 0 && (
                <span className="text-[10px] font-[590] text-accent bg-accent-subtle rounded-[3px] px-1.5 py-0.5">
                  {pendingCount} pending
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="flex items-center gap-3 bg-red-muted rounded-[7px] px-5 py-3.5 mb-6 border border-red/20">
            <AlertTriangle className="w-4 h-4 text-red shrink-0" />
            <span className="text-[13px] text-red flex-1 font-serif">Failed to load approval queue.</span>
            <button
              type="button"
              onClick={loadCards}
              className="text-[11px] font-[510] text-red hover:text-text flex items-center gap-1.5 transition-colors cursor-pointer"
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <RefreshCw className="w-3 h-3" />
              Retry
            </button>
          </div>
        )}

        {/* Filters */}
        <div className="flex items-center gap-1.5 mb-8">
          {(['all', 'pending', 'approved', 'dismissed'] as const).map(f => {
            const count = f === 'all' ? cards.length : cards.filter(c => c.status === f).length;
            return (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`text-[11px] font-[510] px-3 py-1.5 rounded-[5px] transition-colors cursor-pointer ${
                  filter === f
                    ? 'bg-active text-text'
                    : 'text-text-quaternary hover:text-text-secondary hover:bg-hover'
                }`}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
                <span className="ml-1.5 text-text-quaternary">{count}</span>
              </button>
            );
          })}
        </div>

        {/* Content */}
        {loading ? (
          <SkeletonRows count={4} />
        ) : error ? null : filtered.length === 0 ? (
          <EmptyState
            icon={filter === 'pending' ? <ShieldCheck /> : <Inbox />}
            title={
              filter === 'pending'
                ? 'Queue is clear'
                : filter === 'approved'
                ? 'No approved cards'
                : filter === 'dismissed'
                ? 'No dismissed cards'
                : 'No cards yet'
            }
            description={
              filter === 'pending'
                ? 'When the system needs your approval, cards will appear here.'
                : 'Cards matching this filter will appear here.'
            }
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {filtered.map(card => (
              <ApprovalCard
                key={card.id}
                card={card}
                onApprove={(id) => handleAction(id, 'approve')}
                onDismiss={(id) => handleAction(id, 'dismiss')}
              />
            ))}
          </div>
        )}

        {/* Undo toast */}
        {undoState && (
          <UndoToast
            label={`Card ${undoState.action === 'approve' ? 'approved' : 'dismissed'}`}
            onUndo={handleUndo}
            onExpire={handleUndoExpire}
          />
        )}
      </div>

      {/* Inline keyframes for undo toast animation */}
      <style>{`
        @keyframes slideUp {
          from { opacity: 0; transform: translateX(-50%) translateY(12px); }
          to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
      `}</style>
    </div>
  );
}

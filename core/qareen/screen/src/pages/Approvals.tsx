import { useState, useEffect } from 'react';
import { ShieldCheck, Check, X, Clock, AlertTriangle } from 'lucide-react';
import { EmptyState, Tag, StatusDot, SectionHeader, Skeleton, SkeletonRows, ErrorBanner, Button } from '@/components/primitives';
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

function ApprovalCard({ card, onApprove, onDismiss }: { card: Card; onApprove: (id: string) => void; onDismiss: (id: string) => void }) {
  return (
    <div className="bg-bg-secondary rounded-[7px] p-4 border border-border hover:bg-bg-tertiary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Tag label={card.card_type} color={cardTypeColor(card.card_type)} />
          <StatusDot
            color={card.status === 'pending' ? 'yellow' : card.status === 'approved' ? 'green' : card.status === 'dismissed' ? 'gray' : 'red'}
            size="sm"
            label={card.status}
          />
        </div>
        <span className="text-[10px] text-text-quaternary">{timeAgo(card.created_at)}</span>
      </div>
      <h3 className="text-[13px] font-[510] text-text-secondary mb-1">{card.title}</h3>
      <p className="text-[12px] text-text-tertiary mb-3 line-clamp-2">{card.body}</p>
      {card.source_utterance && (
        <p className="text-[10px] text-text-quaternary italic mb-3 border-l-2 border-border pl-2">"{card.source_utterance}"</p>
      )}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          <div className="w-12 h-1 bg-bg-tertiary rounded-full overflow-hidden">
            <div className="h-full rounded-full bg-accent" style={{ width: `${card.confidence * 100}%` }} />
          </div>
          <span className="text-[9px] text-text-quaternary">{Math.round(card.confidence * 100)}%</span>
        </div>
        {card.status === 'pending' && (
          <div className="flex items-center gap-1">
            <button onClick={() => onDismiss(card.id)} className="w-7 h-7 flex items-center justify-center rounded-sm text-text-quaternary hover:text-red hover:bg-hover transition-colors" title="Dismiss"><X className="w-3.5 h-3.5" /></button>
            <button onClick={() => onApprove(card.id)} className="w-7 h-7 flex items-center justify-center rounded-sm text-text-quaternary hover:text-green hover:bg-hover transition-colors" title="Approve"><Check className="w-3.5 h-3.5" /></button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [filter, setFilter] = useState<'all' | 'pending' | 'approved' | 'dismissed'>('all');

  useEffect(() => {
    fetch(`${API}/cards`).then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(data => { setCards(data.cards || data || []); setLoading(false); })
      .catch(() => { setLoading(false); setError(true); });
  }, []);

  const handleAction = async (id: string, action: 'approve' | 'dismiss') => {
    try {
      await fetch(`${API}/cards/${id}/${action}`, { method: 'POST' });
      setCards(prev => prev.map(c => c.id === id ? { ...c, status: action === 'approve' ? 'approved' : 'dismissed' } as Card : c));
    } catch { /* empty */ }
  };

  const filtered = filter === 'all' ? cards : cards.filter(c => c.status === filter);

  return (
    <div className="px-5 md:px-8 py-4 md:py-6 overflow-y-auto h-full">
      <h1 className="type-title mb-6">Approval Queue</h1>

      {error && <ErrorBanner message="Failed to load approval queue." />}

      <div className="flex items-center gap-2 mb-6 flex-wrap">
        {(['all', 'pending', 'approved', 'dismissed'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`text-[11px] font-[510] px-2.5 py-1 rounded-sm transition-colors ${filter === f ? 'bg-active text-text' : 'text-text-quaternary hover:text-text-secondary hover:bg-hover'}`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
            {f !== 'all' && <span className="ml-1 text-text-quaternary">({cards.filter(c => f === 'all' || c.status === f).length})</span>}
          </button>
        ))}
      </div>

      {loading ? (
        <SkeletonRows count={4} />
      ) : filtered.length === 0 ? (
        <EmptyState icon={<ShieldCheck />} title={filter === 'pending' ? 'No pending approvals' : 'No cards'} description="Cards requiring your approval will appear here." />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map(card => (
            <ApprovalCard key={card.id} card={card} onApprove={(id) => handleAction(id, 'approve')} onDismiss={(id) => handleAction(id, 'dismiss')} />
          ))}
        </div>
      )}
    </div>
  );
}

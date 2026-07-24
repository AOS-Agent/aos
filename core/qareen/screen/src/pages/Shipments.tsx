/**
 * Shipments — Auto Tracker dashboard.
 *
 * Top: 'Arriving today' strip. Body: kanban grouped by milestone
 * (label_created → picked_up → in_transit → out_for_delivery → delivered,
 * with an Attention column for off-path states), or grouped by category via
 * the toggle. Pending detection candidates sit above the board with
 * confirm/reject; uncategorized domains get one-click categorization that
 * POSTs a sticky domain rule.
 *
 * Provenance is first-class: email/digest-sourced shipments render with a
 * dashed purple treatment so they are never confused with live carrier data.
 */

import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Package, Search, Plus, Check, X, ChevronDown, ChevronRight,
  CalendarClock, Inbox, TriangleAlert, TagIcon,
} from 'lucide-react';
import {
  useShipments, useShipmentCandidates, useDomainRules,
  useAddShipment, useResolveCandidate, useSetDomainRule,
  type Shipment, type ShipmentCandidate, type Milestone,
  HAPPY_PATH, ATTENTION_MILESTONES,
} from '@/hooks/useShipments';
import { EmptyState, Tag, SkeletonCards, ErrorBanner, Button } from '@/components/primitives';
import {
  MILESTONE_LABELS, milestoneLabel, milestoneColor, SourceBadge,
  formatEta, isEtaToday, shipmentTitle, DEFAULT_CATEGORIES,
} from '@/components/shipments/shared';

// ── Shipment card ───────────────────────────────────────────────────────────

function CategorizePicker({ domain, merchant }: { domain: string; merchant: string | null }) {
  const [open, setOpen] = useState(false);
  const setRule = useSetDomainRule();
  const { data: rules } = useDomainRules();
  const categories = useMemo(() => {
    const existing = (rules ?? []).map((r) => r.category).filter((c): c is string => !!c);
    return Array.from(new Set([...DEFAULT_CATEGORIES, ...existing]));
  }, [rules]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}
        className="inline-flex items-center gap-1 h-5 px-2 rounded-xs text-[11px] font-medium text-text-quaternary border border-dashed border-border-tertiary hover:text-text-tertiary hover:border-border-secondary transition-colors cursor-pointer"
        title={`Categorize ${domain}`}
      >
        <TagIcon className="w-3 h-3" />
        categorize
      </button>
    );
  }
  return (
    <div
      className="flex flex-wrap items-center gap-1 mt-1.5"
      onClick={(e) => e.stopPropagation()}
    >
      {categories.map((c) => (
        <button
          key={c}
          type="button"
          disabled={setRule.isPending}
          onClick={() => setRule.mutate(
            { domain, category: c, display_name: merchant ?? domain },
            { onSuccess: () => setOpen(false) },
          )}
          className="h-5 px-2 rounded-xs text-[11px] font-medium bg-bg-tertiary text-text-tertiary hover:bg-hover hover:text-text transition-colors cursor-pointer disabled:opacity-40"
        >
          {c}
        </button>
      ))}
    </div>
  );
}

function ShipmentCard({ shipment }: { shipment: Shipment }) {
  const navigate = useNavigate();
  const fromEmail = shipment.source === 'email' || shipment.source === 'digest';
  const eta = formatEta(shipment.eta);
  const needsCategory = !!shipment.merchant_domain && !shipment.category;

  return (
    <div
      onClick={() => navigate(`/shipments/${shipment.id}`)}
      className={`
        bg-bg-secondary rounded-[7px] p-3.5 border transition-all cursor-pointer group
        hover:bg-bg-tertiary/50
        ${fromEmail
          ? 'border-dashed border-tag-purple/40 hover:border-tag-purple/60'
          : 'border-border-secondary hover:border-border-tertiary'}
      `}
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <span className="text-[14px] font-[510] text-text truncate">
          {shipmentTitle(shipment)}
        </span>
        <SourceBadge source={shipment.source} />
      </div>
      <div className="flex items-center gap-2 mb-1.5">
        <Tag label={shipment.carrier} color="blue" />
        <span className="text-[11px] font-mono text-text-quaternary truncate">
          {shipment.tracking_number}
        </span>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {eta && (
          <span className="text-[11px] text-text-tertiary flex items-center gap-1">
            <CalendarClock className="w-3 h-3 text-text-quaternary" />
            {eta}
          </span>
        )}
        {shipment.category && <Tag label={shipment.category} color="teal" />}
        {fromEmail && (
          <span className="text-[10px] text-tag-purple">estimated from email</span>
        )}
      </div>
      {needsCategory && (
        <CategorizePicker domain={shipment.merchant_domain!} merchant={shipment.merchant} />
      )}
    </div>
  );
}

// ── Approval queue ──────────────────────────────────────────────────────────

function CandidateRow({ candidate }: { candidate: ShipmentCandidate }) {
  const { confirm, reject } = useResolveCandidate();
  const c = candidate.candidate;
  const sender = (c.source?.sender ?? c.context?.sender_domain) as string | undefined;
  const busy = confirm.isPending || reject.isPending;

  return (
    <div className="flex items-center gap-3 px-3 py-2.5 rounded-[7px] bg-bg-secondary border border-border-secondary">
      <Inbox className="w-4 h-4 text-text-quaternary shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-mono text-text truncate">{c.tracking_number}</span>
          <Tag label={c.carrier} color="blue" />
          <Tag label={c.layer} color="gray" />
        </div>
        <div className="text-[11px] text-text-quaternary mt-0.5 truncate">
          {Math.round((c.confidence ?? 0) * 100)}% confidence
          {sender ? ` · from ${sender}` : ''}
        </div>
      </div>
      <Button
        size="sm" variant="secondary" icon={<Check />}
        disabled={busy}
        onClick={() => confirm.mutate(candidate.id)}
      >
        Confirm
      </Button>
      <Button
        size="sm" variant="ghost" icon={<X />}
        disabled={busy}
        onClick={() => reject.mutate(candidate.id)}
      >
        Reject
      </Button>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

type GroupBy = 'milestone' | 'category';

export default function Shipments() {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('active');
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [domainFilter, setDomainFilter] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<GroupBy>('milestone');
  const [addText, setAddText] = useState('');
  const [queueOpen, setQueueOpen] = useState(true);

  const { data, isLoading, isError } = useShipments({
    status: statusFilter === 'all' ? undefined : statusFilter,
    q: search || undefined,
  });
  const { data: candidates } = useShipmentCandidates();
  const addShipment = useAddShipment();

  const shipments = useMemo(() => data?.shipments ?? [], [data?.shipments]);
  const summary = data?.summary;

  // Client-side category/domain narrowing (kanban needs the full active set).
  const visible = useMemo(() => shipments.filter((s) => {
    if (categoryFilter && (s.category ?? '') !== categoryFilter) return false;
    if (domainFilter && (s.merchant_domain ?? '') !== domainFilter) return false;
    return true;
  }), [shipments, categoryFilter, domainFilter]);

  const arrivingToday = useMemo(
    () => shipments.filter((s) => s.status === 'active' && isEtaToday(s.eta)),
    [shipments],
  );

  const categories = useMemo(
    () => Array.from(new Set(shipments.map((s) => s.category).filter((c): c is string => !!c))).sort(),
    [shipments],
  );
  const domains = useMemo(
    () => Array.from(new Set(shipments.map((s) => s.merchant_domain).filter((d): d is string => !!d))).sort(),
    [shipments],
  );

  const byMilestone = useMemo(() => {
    const map = new Map<Milestone, Shipment[]>();
    for (const m of [...HAPPY_PATH, ...ATTENTION_MILESTONES]) map.set(m, []);
    for (const s of visible) map.get(s.milestone)?.push(s);
    return map;
  }, [visible]);

  const attention = ATTENTION_MILESTONES.flatMap((m) => byMilestone.get(m) ?? []);

  const byCategory = useMemo(() => {
    const map = new Map<string, Shipment[]>();
    for (const s of visible) {
      const key = s.category ?? 'uncategorized';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(s);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [visible]);

  const submitAdd = () => {
    const text = addText.trim();
    if (!text || addShipment.isPending) return;
    addShipment.mutate({ text }, { onSuccess: () => setAddText('') });
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1400px] mx-auto px-6 py-8">
        {/* Header + summary */}
        <div className="flex items-center gap-3 mb-1">
          <h1 className="text-[22px] font-[600] text-text">Shipments</h1>
          {summary && (
            <div className="flex items-center gap-2 ml-2">
              <Tag label={`${summary.active} active`} color="blue" />
              {summary.arriving_today > 0 && <Tag label={`${summary.arriving_today} today`} color="teal" />}
              {summary.exceptions > 0 && <Tag label={`${summary.exceptions} exceptions`} color="red" />}
            </div>
          )}
        </div>
        <p className="text-[13px] text-text-quaternary mb-5">
          Auto-tracked packages from carrier APIs and your inbox.
        </p>

        {isError && <ErrorBanner />}

        {/* Add box — paste anything, detection figures it out */}
        <div className="flex items-center gap-2 mb-6 max-w-[560px]">
          <div className="flex items-center gap-2 flex-1 h-9 px-3 rounded-[7px] bg-bg-secondary border border-border-secondary focus-within:border-border-tertiary transition-colors">
            <Search className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <input
              value={addText}
              onChange={(e) => setAddText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submitAdd(); }}
              placeholder="Paste a tracking number or shipping email text…"
              className="flex-1 bg-transparent outline-none text-[13px] text-text placeholder:text-text-quaternary"
            />
          </div>
          <Button variant="primary" size="md" icon={<Plus />} disabled={!addText.trim() || addShipment.isPending} onClick={submitAdd}>
            Track
          </Button>
        </div>
        {addShipment.isSuccess && addShipment.data?.candidate && !addShipment.data?.shipment && (
          <p className="text-[12px] text-text-tertiary -mt-4 mb-4">
            Low-confidence detection — added to the approval queue below.
          </p>
        )}

        {/* Arriving today strip */}
        {arrivingToday.length > 0 && (
          <div className="mb-6">
            <div className="flex items-center gap-2 px-1 mb-2">
              <CalendarClock className="w-3.5 h-3.5 text-teal" />
              <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">
                Arriving today
              </span>
              <span className="text-[12px] font-mono text-text-quaternary">{arrivingToday.length}</span>
            </div>
            <div className="flex gap-3 overflow-x-auto pb-1">
              {arrivingToday.map((s) => (
                <div key={s.id} className="w-[260px] shrink-0">
                  <ShipmentCard shipment={s} />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Approval queue */}
        {(candidates?.length ?? 0) > 0 && (
          <div className="mb-6">
            <button
              type="button"
              onClick={() => setQueueOpen(!queueOpen)}
              className="flex items-center gap-2 px-1 mb-2 cursor-pointer"
            >
              {queueOpen
                ? <ChevronDown className="w-3 h-3 text-text-quaternary" />
                : <ChevronRight className="w-3 h-3 text-text-quaternary" />}
              <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">
                Needs approval
              </span>
              <span className="text-[12px] font-mono text-text-quaternary">{candidates!.length}</span>
            </button>
            {queueOpen && (
              <div className="space-y-2">
                {candidates!.map((c) => <CandidateRow key={c.id} candidate={c} />)}
              </div>
            )}
          </div>
        )}

        {/* Controls */}
        <div className="flex items-center gap-2 flex-wrap mb-5">
          {/* Group-by toggle */}
          <div className="flex items-center gap-1 h-8 px-1 rounded-full bg-bg-secondary border border-border-secondary">
            {(['milestone', 'category'] as GroupBy[]).map((g) => (
              <button
                key={g}
                type="button"
                onClick={() => setGroupBy(g)}
                className={`px-3 h-6 rounded-full text-[12px] font-[510] cursor-pointer transition-all duration-150 ${
                  groupBy === g ? 'bg-bg-tertiary text-text' : 'text-text-tertiary hover:text-text-secondary'
                }`}
              >
                {g === 'milestone' ? 'Board' : 'By category'}
              </button>
            ))}
          </div>

          {/* Status filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="h-8 px-2 rounded-[6px] bg-bg-secondary border border-border-secondary text-[12px] text-text-secondary outline-none cursor-pointer"
          >
            <option value="active">Active</option>
            <option value="delivered">Delivered</option>
            <option value="archived">Archived</option>
            <option value="all">All</option>
          </select>

          {/* Category chips */}
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategoryFilter(categoryFilter === c ? null : c)}
              className={`h-8 px-3 rounded-full text-[12px] font-[510] cursor-pointer transition-colors border ${
                categoryFilter === c
                  ? 'bg-bg-tertiary text-text border-border-tertiary'
                  : 'bg-bg-secondary text-text-tertiary border-border-secondary hover:text-text-secondary'
              }`}
            >
              {c}
            </button>
          ))}

          {/* Domain filter */}
          {domains.length > 0 && (
            <select
              value={domainFilter ?? ''}
              onChange={(e) => setDomainFilter(e.target.value || null)}
              className="h-8 px-2 rounded-[6px] bg-bg-secondary border border-border-secondary text-[12px] text-text-secondary outline-none cursor-pointer"
            >
              <option value="">All domains</option>
              {domains.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          )}

          {/* Search within results */}
          <div className="flex items-center gap-2 h-8 px-3 rounded-[6px] bg-bg-secondary border border-border-secondary ml-auto">
            <Search className="w-3 h-3 text-text-quaternary shrink-0" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter…"
              className="w-[140px] bg-transparent outline-none text-[12px] text-text placeholder:text-text-quaternary"
            />
          </div>
        </div>

        {/* Body */}
        {isLoading ? (
          <SkeletonCards count={6} />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={<Package />}
            title="No shipments"
            description={shipments.length === 0
              ? 'Paste a tracking number above, or let Auto Tracker pick them up from your email.'
              : 'Nothing matches the current filters.'}
          />
        ) : groupBy === 'milestone' ? (
          /* Kanban by milestone */
          <div className="flex gap-3 overflow-x-auto pb-4 items-start">
            {HAPPY_PATH.map((m) => {
              const items = byMilestone.get(m) ?? [];
              return (
                <div key={m} className="w-[240px] shrink-0">
                  <div className="flex items-center gap-2 px-1 mb-2">
                    <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">
                      {MILESTONE_LABELS[m]}
                    </span>
                    <span className="text-[12px] font-mono text-text-quaternary">{items.length}</span>
                  </div>
                  <div className="space-y-2">
                    {items.map((s) => <ShipmentCard key={s.id} shipment={s} />)}
                  </div>
                </div>
              );
            })}
            {attention.length > 0 && (
              <div className="w-[240px] shrink-0">
                <div className="flex items-center gap-2 px-1 mb-2">
                  <TriangleAlert className="w-3.5 h-3.5 text-red" />
                  <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">
                    Attention
                  </span>
                  <span className="text-[12px] font-mono text-text-quaternary">{attention.length}</span>
                </div>
                <div className="space-y-2">
                  {attention.map((s) => (
                    <div key={s.id}>
                      <div className="px-1 mb-1">
                        <Tag label={milestoneLabel(s.milestone)} color={milestoneColor(s.milestone)} />
                      </div>
                      <ShipmentCard shipment={s} />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          /* Grouped by category */
          <div>
            {byCategory.map(([category, items]) => (
              <div key={category} className="mb-6">
                <div className="flex items-center gap-2 px-1 mb-2">
                  <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">
                    {category}
                  </span>
                  <span className="text-[12px] font-mono text-text-quaternary">{items.length}</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {items.map((s) => <ShipmentCard key={s.id} shipment={s} />)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

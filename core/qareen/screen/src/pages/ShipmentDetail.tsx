/**
 * ShipmentDetail — full-canvas shipment view.
 *
 * Scan timeline (per-event location + description), linked tracking numbers
 * for carrier handoffs, and the linked order's items ("what's in the box")
 * when present. Label edit, category set, and archive via PATCH.
 */

import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft, Package, MapPin, Link2, Box, Archive, ArchiveRestore,
  Pencil, Check, X, CalendarClock,
} from 'lucide-react';
import { useShipment, useUpdateShipment, type ShipmentEvent } from '@/hooks/useShipments';
import { Tag, SkeletonRows, ErrorBanner, Button, EmptyState } from '@/components/primitives';
import {
  milestoneLabel, milestoneColor, milestoneDot, SourceBadge,
  formatEta, formatEventTime, shipmentTitle, DEFAULT_CATEGORIES,
} from '@/components/shipments/shared';

function EventRow({ event, last }: { event: ShipmentEvent; last: boolean }) {
  return (
    <div className="flex gap-3">
      {/* Rail */}
      <div className="flex flex-col items-center">
        <span className={`w-2.5 h-2.5 rounded-full mt-1 shrink-0 ${milestoneDot(event.milestone)}`} />
        {!last && <span className="flex-1 w-px bg-border-secondary mt-1" />}
      </div>
      <div className="pb-5 flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          {event.milestone && (
            <Tag label={milestoneLabel(event.milestone)} color={milestoneColor(event.milestone)} />
          )}
          <span className="text-[11px] font-mono text-text-quaternary">
            {formatEventTime(event.timestamp)}
          </span>
        </div>
        {event.description && (
          <p className="text-[13px] text-text-secondary mt-1">{event.description}</p>
        )}
        {event.location && (
          <p className="text-[12px] text-text-quaternary mt-0.5 flex items-center gap-1">
            <MapPin className="w-3 h-3" />
            {event.location}
          </p>
        )}
      </div>
    </div>
  );
}

export default function ShipmentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, isError } = useShipment(id);
  const update = useUpdateShipment();

  const [editingLabel, setEditingLabel] = useState(false);
  const [labelDraft, setLabelDraft] = useState('');
  const [pickingCategory, setPickingCategory] = useState(false);

  if (isLoading) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="max-w-[880px] mx-auto px-6 py-8">
          <SkeletonRows count={6} />
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="max-w-[880px] mx-auto px-6 py-8">
          <button
            type="button"
            onClick={() => navigate('/shipments')}
            className="flex items-center gap-1.5 text-[13px] text-text-quaternary hover:text-text-tertiary cursor-pointer mb-6"
          >
            <ArrowLeft className="w-3 h-3" />
            Shipments
          </button>
          <ErrorBanner message="Couldn't load this shipment." />
        </div>
      </div>
    );
  }

  const { shipment, events, numbers, order } = data;
  const eta = formatEta(shipment.eta);
  const archived = shipment.status === 'archived';
  const handoffs = numbers.filter((n) => n.role !== 'primary' || n.number !== shipment.tracking_number);
  const categories = Array.from(new Set([...DEFAULT_CATEGORIES, shipment.category].filter((c): c is string => !!c)));

  // Newest first; fall back to seq when timestamps are missing.
  const timeline = [...events].sort((a, b) =>
    (b.timestamp ?? '').localeCompare(a.timestamp ?? '') || (b.seq ?? 0) - (a.seq ?? 0),
  );

  const saveLabel = () => {
    const label = labelDraft.trim();
    update.mutate({ id: shipment.id, label }, { onSuccess: () => setEditingLabel(false) });
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[880px] mx-auto px-6 py-8">
        {/* Back */}
        <button
          type="button"
          onClick={() => navigate('/shipments')}
          className="flex items-center gap-1.5 text-[13px] text-text-quaternary hover:text-text-tertiary cursor-pointer"
          style={{ transitionDuration: 'var(--duration-instant)' }}
        >
          <ArrowLeft className="w-3 h-3" />
          Shipments
        </button>

        {/* Header */}
        <div className="mt-6 mb-6">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <Tag label={shipment.carrier} color="blue" />
            <Tag label={milestoneLabel(shipment.milestone)} color={milestoneColor(shipment.milestone)} />
            <SourceBadge source={shipment.source} />
            {archived && <Tag label="archived" color="gray" />}
            {shipment.category && <Tag label={shipment.category} color="teal" />}
          </div>
          <div className="flex items-center gap-2">
            {editingLabel ? (
              <div className="flex items-center gap-2">
                <input
                  autoFocus
                  value={labelDraft}
                  onChange={(e) => setLabelDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') saveLabel();
                    if (e.key === 'Escape') setEditingLabel(false);
                  }}
                  placeholder="Label this shipment…"
                  className="h-9 px-3 rounded-[6px] bg-bg-secondary border border-border-secondary text-[15px] text-text outline-none focus:border-border-tertiary"
                />
                <Button size="sm" variant="primary" icon={<Check />} disabled={update.isPending} onClick={saveLabel}>
                  Save
                </Button>
                <Button size="sm" variant="ghost" icon={<X />} onClick={() => setEditingLabel(false)}>
                  Cancel
                </Button>
              </div>
            ) : (
              <>
                <h1 className="text-[22px] font-[600] text-text">{shipmentTitle(shipment)}</h1>
                <button
                  type="button"
                  onClick={() => { setLabelDraft(shipment.label ?? ''); setEditingLabel(true); }}
                  className="text-text-quaternary hover:text-text-tertiary cursor-pointer"
                  title="Edit label"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
              </>
            )}
          </div>
          <p className="text-[13px] font-mono text-text-quaternary mt-1">{shipment.tracking_number}</p>
          {eta && (
            <p className="text-[13px] text-text-tertiary mt-1.5 flex items-center gap-1.5">
              <CalendarClock className="w-3.5 h-3.5 text-text-quaternary" />
              ETA {eta}
            </p>
          )}
          {(shipment.source === 'email' || shipment.source === 'digest') && (
            <p className="text-[12px] text-tag-purple mt-1.5">
              Detected from email — details are estimated until the carrier API confirms.
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 mb-8 flex-wrap">
          <Button
            size="sm" variant="secondary"
            icon={archived ? <ArchiveRestore /> : <Archive />}
            disabled={update.isPending}
            onClick={() => update.mutate({ id: shipment.id, status: archived ? 'active' : 'archived' })}
          >
            {archived ? 'Unarchive' : 'Archive'}
          </Button>
          <div className="relative">
            <Button size="sm" variant="ghost" onClick={() => setPickingCategory(!pickingCategory)}>
              {shipment.category ? `Category: ${shipment.category}` : 'Set category'}
            </Button>
          </div>
          {pickingCategory && (
            <div className="flex items-center gap-1 flex-wrap">
              {categories.map((c) => (
                <button
                  key={c}
                  type="button"
                  disabled={update.isPending}
                  onClick={() => update.mutate({ id: shipment.id, category: c }, { onSuccess: () => setPickingCategory(false) })}
                  className="h-7 px-2.5 rounded-full text-[12px] font-[510] bg-bg-secondary border border-border-secondary text-text-tertiary hover:text-text hover:bg-bg-tertiary transition-colors cursor-pointer disabled:opacity-40"
                >
                  {c}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* What's in the box */}
        {order && order.items.length > 0 && (
          <div className="mb-8">
            <div className="flex items-center gap-2 px-1 mb-2">
              <Box className="w-3.5 h-3.5 text-text-quaternary" />
              <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">
                What's in the box
              </span>
              {order.order_number && (
                <span className="text-[11px] font-mono text-text-quaternary">
                  order {order.order_number}
                </span>
              )}
            </div>
            <div className="bg-bg-secondary rounded-[7px] border border-border-secondary divide-y divide-border-secondary">
              {order.items.map((item, i) => (
                <div key={item.id ?? i} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="flex-1 min-w-0 text-[13px] text-text-secondary truncate">{item.name}</span>
                  {item.qty > 1 && (
                    <span className="text-[12px] font-mono text-text-quaternary">×{item.qty}</span>
                  )}
                  {item.price != null && (
                    <span className="text-[12px] font-mono text-text-quaternary">
                      {order.currency ?? '$'}{item.price.toFixed(2)}
                    </span>
                  )}
                </div>
              ))}
              {order.total != null && (
                <div className="flex items-center gap-3 px-4 py-2.5">
                  <span className="flex-1 text-[12px] font-[590] text-text-tertiary">Total</span>
                  <span className="text-[12px] font-mono text-text-secondary">
                    {order.currency ?? '$'}{order.total.toFixed(2)}
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Linked numbers — carrier handoffs */}
        {handoffs.length > 0 && (
          <div className="mb-8">
            <div className="flex items-center gap-2 px-1 mb-2">
              <Link2 className="w-3.5 h-3.5 text-text-quaternary" />
              <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">
                Linked numbers
              </span>
            </div>
            <div className="space-y-1.5">
              {handoffs.map((n, i) => (
                <div
                  key={`${n.carrier}-${n.number}-${i}`}
                  className="flex items-center gap-2 px-3 py-2 rounded-[7px] bg-bg-secondary border border-border-secondary"
                >
                  <Tag label={n.carrier} color="purple" />
                  <span className="text-[12px] font-mono text-text-secondary truncate">{n.number}</span>
                  <span className="text-[10px] text-text-quaternary ml-auto shrink-0">
                    {n.role === 'handoff' ? 'handoff' : n.role}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Scan timeline */}
        <div className="mb-4">
          <div className="flex items-center gap-2 px-1 mb-3">
            <Package className="w-3.5 h-3.5 text-text-quaternary" />
            <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-text-tertiary">
              Scans
            </span>
            <span className="text-[12px] font-mono text-text-quaternary">{timeline.length}</span>
          </div>
          {timeline.length === 0 ? (
            <EmptyState
              icon={<Package />}
              title="No scans yet"
              description="Carrier scans appear here after the first poll."
            />
          ) : (
            <div>
              {timeline.map((e, i) => (
                <EventRow key={e.id ?? i} event={e} last={i === timeline.length - 1} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

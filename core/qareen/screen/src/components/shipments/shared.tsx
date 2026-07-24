// ---------------------------------------------------------------------------
// Shared shipment UI bits — milestone labels/colors, the source badge, and
// eta formatting used by both the board and the detail page.
// ---------------------------------------------------------------------------

import { Mail, Radio, PenLine } from 'lucide-react';
import { parseISO, isToday, isTomorrow, format } from 'date-fns';
import { Tag, type TagColor } from '@/components/primitives';
import type { Milestone } from '@/hooks/useShipments';

export const MILESTONE_LABELS: Record<Milestone, string> = {
  label_created: 'Label created',
  picked_up: 'Picked up',
  in_transit: 'In transit',
  out_for_delivery: 'Out for delivery',
  delivered: 'Delivered',
  exception: 'Exception',
  failed_attempt: 'Failed attempt',
  returned: 'Returned',
  expired: 'Expired',
};

export function milestoneLabel(m: Milestone | null | undefined): string {
  return m ? MILESTONE_LABELS[m] ?? m : 'Scan';
}

export function milestoneColor(m: Milestone | null | undefined): TagColor {
  switch (m) {
    case 'delivered': return 'green';
    case 'out_for_delivery': return 'teal';
    case 'in_transit': return 'blue';
    case 'picked_up': return 'purple';
    case 'exception':
    case 'failed_attempt': return 'red';
    case 'returned':
    case 'expired': return 'orange';
    default: return 'gray';
  }
}

export function milestoneDot(m: Milestone | null | undefined): string {
  switch (m) {
    case 'delivered': return 'bg-green';
    case 'out_for_delivery': return 'bg-teal';
    case 'in_transit': return 'bg-accent';
    case 'picked_up': return 'bg-purple';
    case 'exception':
    case 'failed_attempt': return 'bg-red';
    case 'returned':
    case 'expired': return 'bg-orange';
    default: return 'bg-text-quaternary';
  }
}

/**
 * Source badge — provenance matters. `email`/`digest` detections are
 * ESTIMATES parsed from merchant mail, not live carrier scans: they get a
 * dashed-border purple treatment so nobody reads them as authoritative.
 */
export function SourceBadge({ source }: { source: string }) {
  if (source === 'api') {
    return (
      <span className="inline-flex items-center gap-1 h-5 px-2 rounded-xs text-[11px] font-medium text-tag-green bg-tag-green-bg border border-transparent">
        <Radio className="w-3 h-3" />
        live
      </span>
    );
  }
  if (source === 'email' || source === 'digest') {
    return (
      <span
        className="inline-flex items-center gap-1 h-5 px-2 rounded-xs text-[11px] font-medium text-tag-purple bg-tag-purple-bg border border-dashed"
        title="Detected from email — estimated, not live carrier data"
      >
        <Mail className="w-3 h-3" />
        email
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 h-5 px-2 rounded-xs text-[11px] font-medium text-tag-gray bg-tag-gray-bg">
      <PenLine className="w-3 h-3" />
      manual
    </span>
  );
}

export function formatEta(eta: string | null | undefined): string | null {
  if (!eta) return null;
  try {
    const d = parseISO(eta);
    if (isToday(d)) return `Today · ${format(d, 'h:mm a')}`;
    if (isTomorrow(d)) return `Tomorrow · ${format(d, 'MMM d')}`;
    return format(d, 'EEE, MMM d');
  } catch {
    return eta;
  }
}

export function isEtaToday(eta: string | null | undefined): boolean {
  if (!eta) return false;
  try {
    return isToday(parseISO(eta));
  } catch {
    return false;
  }
}

export function formatEventTime(ts: string | null | undefined): string {
  if (!ts) return '';
  try {
    return format(parseISO(ts), 'MMM d · h:mm a');
  } catch {
    return ts;
  }
}

/** Fixed starting set for one-click domain categorization; rules already in
 *  the store add their own categories to the picker dynamically. */
export const DEFAULT_CATEGORIES = [
  'shopping',
  'tech',
  'clothing',
  'health',
  'home',
  'food',
  'hobby',
  'other',
];

/** Display title for a shipment card: user label > merchant > tracking number. */
export function shipmentTitle(s: { label: string | null; merchant: string | null; merchant_domain: string | null; tracking_number: string }): string {
  return s.label ?? s.merchant ?? s.merchant_domain ?? s.tracking_number;
}

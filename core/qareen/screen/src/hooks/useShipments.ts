// ---------------------------------------------------------------------------
// Shipments — Auto Tracker data layer.
//
// Mirrors the fixed API contract in core/qareen/api/shipments.py. Shapes match
// the tracking store rows (shipments / shipment_events / shipment_numbers /
// orders+order_items / shipment_candidates / detection_eval / domain_rules).
// Response normalizers tolerate both snake_case row payloads and the
// already-shaped contract responses so the UI never hard-breaks on either.
// ---------------------------------------------------------------------------

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

// ── Domain types ────────────────────────────────────────────────────────────

/** Canonical milestones (tracking.models.Milestone). Happy path first. */
export type Milestone =
  | 'label_created'
  | 'picked_up'
  | 'in_transit'
  | 'out_for_delivery'
  | 'delivered'
  | 'exception'
  | 'failed_attempt'
  | 'returned'
  | 'expired';

export const HAPPY_PATH: Milestone[] = [
  'label_created',
  'picked_up',
  'in_transit',
  'out_for_delivery',
  'delivered',
];

/** Off-path milestones — grouped under the "Attention" column/section. */
export const ATTENTION_MILESTONES: Milestone[] = [
  'exception',
  'failed_attempt',
  'returned',
  'expired',
];

export type ShipmentStatus = 'active' | 'delivered' | 'expired' | 'archived';

/** Provenance. `email`/`digest` come from message detection, NOT the carrier
 *  API — the UI must render them as estimated, never as live carrier data. */
export type ShipmentSource = 'api' | 'email' | 'manual' | 'digest';

export interface Shipment {
  id: string;
  tracking_number: string;
  carrier: string;
  direction: 'inbound' | 'outbound' | 'return' | string;
  milestone: Milestone;
  status: ShipmentStatus;
  source: ShipmentSource | string;
  eta: string | null;
  merchant: string | null;
  merchant_domain: string | null;
  category: string | null;
  label: string | null;
  confidence: number;
  first_seen: string | null;
  created?: string | null;
  updated?: string | null;
}

export interface ShipmentEvent {
  id?: number;
  milestone: Milestone | null;
  description: string;
  timestamp: string | null;
  fetched_at?: string | null;
  location: string | null;
  seq?: number;
  carrier_code?: string | null;
}

export interface ShipmentNumber {
  carrier: string;
  number: string;
  role: 'primary' | 'handoff' | string;
}

export interface OrderItem {
  id?: number;
  name: string;
  qty: number;
  price?: number | null;
  sku?: string | null;
  image_url?: string | null;
}

export interface ShipmentOrder {
  id?: string;
  merchant?: string | null;
  merchant_domain?: string | null;
  order_number?: string | null;
  order_date?: string | null;
  total?: number | null;
  currency?: string | null;
  items: OrderItem[];
}

export interface ShipmentsSummary {
  active: number;
  arriving_today: number;
  exceptions: number;
}

export interface ShipmentsResponse {
  shipments: Shipment[];
  summary: ShipmentsSummary;
}

export interface ShipmentDetailResponse {
  shipment: Shipment;
  events: ShipmentEvent[];
  numbers: ShipmentNumber[];
  order: ShipmentOrder | null;
}

/** Detection payload embedded in a candidate (detect.DetectionCandidate). */
export interface CandidatePayload {
  tracking_number: string;
  carrier: string;
  confidence: number;
  layer: string;
  source?: Record<string, unknown>;
  sources?: Record<string, unknown>[];
  context?: Record<string, unknown>;
}

export interface ShipmentCandidate {
  id: string;
  candidate: CandidatePayload;
  layer: string;
  confidence: number | null;
  status: 'pending' | 'confirmed' | 'rejected' | string;
  created: string | null;
}

export interface DomainRule {
  domain: string;
  category: string | null;
  display_name: string | null;
  created?: string;
  updated?: string;
}

export interface EvalCandidate {
  id: number | string;
  candidate: CandidatePayload;
  layer: string;
  predicted: string | null;
}

/** POST /api/shipments returns one of these two shapes. */
export interface AddShipmentResult {
  shipment?: Shipment;
  candidate?: ShipmentCandidate | CandidatePayload;
}

// ── Normalizers (tolerant to raw store rows) ────────────────────────────────

function asString(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null;
}

function normalizeShipment(raw: Record<string, unknown>): Shipment {
  return {
    id: String(raw.id ?? ''),
    tracking_number: String(raw.tracking_number ?? ''),
    carrier: String(raw.carrier ?? ''),
    direction: String(raw.direction ?? 'inbound'),
    milestone: (asString(raw.milestone) ?? 'label_created') as Milestone,
    status: (asString(raw.status) ?? 'active') as ShipmentStatus,
    source: asString(raw.source) ?? 'manual',
    eta: asString(raw.eta),
    merchant: asString(raw.merchant),
    merchant_domain: asString(raw.merchant_domain),
    category: asString(raw.category),
    label: asString(raw.label),
    confidence: typeof raw.confidence === 'number' ? raw.confidence : 1,
    first_seen: asString(raw.first_seen),
    created: asString(raw.created) ?? asString(raw.created_at),
    updated: asString(raw.updated) ?? asString(raw.updated_at),
  };
}

/** Candidates/eval rows may carry the payload as an object (`candidate`) or
 *  as a JSON string column (`candidate_json`). */
function normalizeCandidatePayload(raw: Record<string, unknown>): CandidatePayload {
  let payload: unknown = raw.candidate ?? raw.candidate_json ?? raw;
  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload);
    } catch {
      payload = {};
    }
  }
  const p = (payload ?? {}) as Record<string, unknown>;
  return {
    tracking_number: String(p.tracking_number ?? ''),
    carrier: String(p.carrier ?? ''),
    confidence: typeof p.confidence === 'number' ? p.confidence : 0,
    layer: String(p.layer ?? raw.layer ?? ''),
    source: (p.source ?? undefined) as Record<string, unknown> | undefined,
    sources: (p.sources ?? undefined) as Record<string, unknown>[] | undefined,
    context: (p.context ?? undefined) as Record<string, unknown> | undefined,
  };
}

function normalizeCandidate(raw: Record<string, unknown>): ShipmentCandidate {
  return {
    id: String(raw.id ?? ''),
    candidate: normalizeCandidatePayload(raw),
    layer: String(raw.layer ?? ''),
    confidence: typeof raw.confidence === 'number' ? raw.confidence : null,
    status: String(raw.status ?? 'pending'),
    created: asString(raw.created),
  };
}

function normalizeEvalCandidate(raw: Record<string, unknown>): EvalCandidate {
  return {
    id: (raw.id as number | string) ?? '',
    candidate: normalizeCandidatePayload(raw),
    layer: String(raw.layer ?? ''),
    predicted: asString(raw.predicted),
  };
}

function unwrapList<T>(data: unknown, key: string, map: (r: Record<string, unknown>) => T): T[] {
  const d = data as Record<string, unknown> | T[];
  const list = Array.isArray(d) ? d : ((d?.[key] as unknown[]) ?? []);
  return (list as Record<string, unknown>[]).map(map);
}

// ── Queries ─────────────────────────────────────────────────────────────────

export interface ShipmentFilters {
  status?: string;
  milestone?: string;
  category?: string;
  q?: string;
}

export function useShipments(filters: ShipmentFilters = {}) {
  const params = new URLSearchParams();
  if (filters.status) params.set('status', filters.status);
  if (filters.milestone) params.set('milestone', filters.milestone);
  if (filters.category) params.set('category', filters.category);
  if (filters.q) params.set('q', filters.q);
  const qs = params.toString();

  return useQuery({
    queryKey: ['shipments', filters],
    queryFn: async (): Promise<ShipmentsResponse> => {
      const data = await api.get<Record<string, unknown>>(`/shipments${qs ? `?${qs}` : ''}`);
      return {
        shipments: unwrapList(data, 'shipments', normalizeShipment),
        summary: (data.summary as ShipmentsSummary) ?? { active: 0, arriving_today: 0, exceptions: 0 },
      };
    },
    staleTime: 30_000,
  });
}

export function useShipment(id: string | undefined) {
  return useQuery({
    queryKey: ['shipment', id],
    enabled: !!id,
    queryFn: async (): Promise<ShipmentDetailResponse> => {
      const data = await api.get<Record<string, unknown>>(`/shipments/${encodeURIComponent(id!)}`);
      const rawOrder = data.order as Record<string, unknown> | null;
      return {
        shipment: normalizeShipment((data.shipment ?? {}) as Record<string, unknown>),
        events: unwrapList(data, 'events', (r) => ({
          id: r.id as number | undefined,
          milestone: asString(r.milestone) as Milestone | null,
          description: String(r.description ?? ''),
          timestamp: asString(r.timestamp),
          fetched_at: asString(r.fetched_at),
          location: asString(r.location),
          seq: r.seq as number | undefined,
          carrier_code: asString(r.carrier_code),
        })),
        numbers: unwrapList(data, 'numbers', (r) => ({
          carrier: String(r.carrier ?? ''),
          number: String(r.number ?? ''),
          role: String(r.role ?? 'handoff'),
        })),
        order: rawOrder
          ? {
              id: asString(rawOrder.id) ?? undefined,
              merchant: asString(rawOrder.merchant),
              merchant_domain: asString(rawOrder.merchant_domain),
              order_number: asString(rawOrder.order_number),
              order_date: asString(rawOrder.order_date),
              total: typeof rawOrder.total === 'number' ? rawOrder.total : null,
              currency: asString(rawOrder.currency),
              items: (Array.isArray(rawOrder.items) ? rawOrder.items : []).map((i) => {
                const item = i as Record<string, unknown>;
                return {
                  id: item.id as number | undefined,
                  name: String(item.name ?? ''),
                  qty: typeof item.qty === 'number' ? item.qty : 1,
                  price: typeof item.price === 'number' ? item.price : null,
                  sku: asString(item.sku),
                  image_url: asString(item.image_url),
                };
              }),
            }
          : null,
      };
    },
    staleTime: 30_000,
  });
}

export function useShipmentCandidates() {
  return useQuery({
    queryKey: ['shipment-candidates'],
    queryFn: async (): Promise<ShipmentCandidate[]> => {
      const data = await api.get<unknown>('/shipments/candidates');
      return unwrapList(data, 'candidates', normalizeCandidate).filter((c) => c.status === 'pending');
    },
    staleTime: 15_000,
  });
}

export function useDomainRules() {
  return useQuery({
    queryKey: ['shipment-domain-rules'],
    queryFn: async (): Promise<DomainRule[]> => {
      const data = await api.get<unknown>('/shipments/domain-rules');
      return unwrapList(data, 'rules', (r) => ({
        domain: String(r.domain ?? ''),
        category: asString(r.category),
        display_name: asString(r.display_name),
        created: asString(r.created) ?? undefined,
        updated: asString(r.updated) ?? undefined,
      }));
    },
    staleTime: 60_000,
  });
}

export function useEvalQueue() {
  return useQuery({
    queryKey: ['shipment-eval'],
    queryFn: async (): Promise<EvalCandidate[]> => {
      const data = await api.get<unknown>('/shipments/eval');
      return unwrapList(data, 'candidates', normalizeEvalCandidate);
    },
    staleTime: 15_000,
  });
}

// ── Mutations ───────────────────────────────────────────────────────────────

function useInvalidateShipments() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ['shipments'] });
    qc.invalidateQueries({ queryKey: ['shipment'] });
    qc.invalidateQueries({ queryKey: ['shipment-candidates'] });
    qc.invalidateQueries({ queryKey: ['shipment-domain-rules'] });
  };
}

/** Manual add / paste box: {text} for auto-detect or {tracking_number, carrier?}. */
export function useAddShipment() {
  const invalidate = useInvalidateShipments();
  return useMutation({
    mutationFn: (body: { text?: string; tracking_number?: string; carrier?: string }) =>
      api.post<AddShipmentResult>('/shipments', body),
    onSuccess: invalidate,
  });
}

export function useUpdateShipment() {
  const invalidate = useInvalidateShipments();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; label?: string; category?: string; status?: string }) =>
      api.patch<{ shipment: Shipment }>(`/shipments/${encodeURIComponent(id)}`, body),
    onSuccess: invalidate,
  });
}

export function useResolveCandidate() {
  const invalidate = useInvalidateShipments();
  const confirm = useMutation({
    mutationFn: (id: string) => api.post(`/shipments/candidates/${encodeURIComponent(id)}/confirm`),
    onSuccess: invalidate,
  });
  const reject = useMutation({
    mutationFn: (id: string) => api.post(`/shipments/candidates/${encodeURIComponent(id)}/reject`),
    onSuccess: invalidate,
  });
  return { confirm, reject };
}

/** One-click domain categorization — the rule sticks for future detections. */
export function useSetDomainRule() {
  const invalidate = useInvalidateShipments();
  return useMutation({
    mutationFn: (body: { domain: string; category: string; display_name?: string }) =>
      api.post<DomainRule>('/shipments/domain-rules', body),
    onSuccess: invalidate,
  });
}

export function useLabelEvalCandidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, label }: { id: number | string; label: 'correct' | 'incorrect' | 'missed' }) =>
      api.post(`/shipments/eval/${encodeURIComponent(String(id))}/label`, { label }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['shipment-eval'] }),
  });
}

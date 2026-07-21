/**
 * Inbox triage — the first-class intake surface, shared by every Work view.
 *
 * Inbox items (manual captures + ambient commitment proposals) render here with
 * inline triage: promote → task (with a project picker), snooze, dismiss. Ambient
 * items carry a provenance receipt ("from iMessage · Jul 12") linking back to the
 * message they were mined from. Deleting is the triage decision — the ambient
 * proposer keeps its comms.db stamp so a dismissed item is never re-proposed.
 *
 * Design language: charcoal + bone. Agent-proposed items carry a quiet purple
 * edge (the system's "agent" hue); manual captures stay neutral.
 */

import { useState } from 'react';
import { ArrowUpRight, Clock, X, Inbox as InboxIcon, ChevronRight } from 'lucide-react';
import { type InboxItem, parseInboxReceipt, useInboxTriage } from '@/hooks/useWork';

const CHANNEL_LABEL: Record<string, string> = {
  im: 'iMessage', wa: 'WhatsApp', tg: 'Telegram', sl: 'Slack',
  em: 'Email', sms: 'SMS', gm: 'Gmail',
};

function readableChannel(ref: string): string {
  const prefix = ref.split('-')[0]?.toLowerCase() ?? '';
  return CHANNEL_LABEL[prefix] ?? 'comms';
}

function fmtDate(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/** A single inbox item with inline triage actions. */
export function InboxItemCard({ item, projects = [] }: { item: InboxItem; projects?: string[] }) {
  const { promote, dismiss, snooze } = useInboxTriage();
  const [picking, setPicking] = useState(false);
  const receipt = parseInboxReceipt(item);
  const isAmbient = item.source === 'ambient-commitment';
  const body = receipt ? receipt.body : item.text;

  const doPromote = (project?: string | null) => {
    promote.mutate({ id: item.id, project: project ?? null });
    setPicking(false);
  };

  return (
    <div
      className="group rounded-lg p-3 bg-bg-secondary border transition-colors duration-75"
      style={{ borderColor: isAmbient ? 'rgba(191,90,242,0.28)' : 'var(--border, rgba(255,245,235,0.08))' }}
    >
      <div className="flex items-start gap-2">
        {isAmbient && <span className="mt-[6px] w-[6px] h-[6px] rounded-full shrink-0 bg-purple" title="Agent-proposed" />}
        <p className="flex-1 min-w-0 text-[14px] leading-[1.5] text-text-secondary">{body}</p>
      </div>

      {/* Provenance receipt */}
      {receipt && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-text-quaternary">
          <span className="px-1.5 py-0.5 rounded bg-bg-tertiary">
            from {readableChannel(receipt.ref)} · {fmtDate(receipt.date)}
          </span>
          <span className="font-mono opacity-60">{receipt.ref}</span>
        </div>
      )}

      {/* Triage actions — appear on hover, always visible on touch */}
      <div className="mt-2.5 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-75">
        {picking ? (
          <div className="flex items-center gap-1 flex-wrap">
            <span className="text-[11px] text-text-quaternary mr-0.5">To:</span>
            <button onClick={() => doPromote(null)}
              className="h-6 px-2 rounded-md text-[12px] text-text-tertiary bg-bg-tertiary hover:bg-bg-quaternary cursor-pointer transition-colors">Unassigned</button>
            {projects.map(p => (
              <button key={p} onClick={() => doPromote(p)}
                className="h-6 px-2 rounded-md text-[12px] text-text-tertiary bg-bg-tertiary hover:bg-bg-quaternary cursor-pointer transition-colors">{p}</button>
            ))}
            <button onClick={() => setPicking(false)} aria-label="Cancel"
              className="h-6 w-6 rounded-md flex items-center justify-center text-text-quaternary hover:text-text cursor-pointer"><X className="w-3.5 h-3.5" /></button>
          </div>
        ) : (
          <>
            <button onClick={() => (projects.length ? setPicking(true) : doPromote(null))}
              className="h-6 px-2 flex items-center gap-1 rounded-md text-[12px] font-[510] text-on-accent bg-accent hover:bg-accent-hover cursor-pointer transition-colors">
              <ArrowUpRight className="w-3.5 h-3.5" />Promote
            </button>
            <button onClick={() => snooze.mutate({ id: item.id })} title="Snooze 1 day"
              className="h-6 px-2 flex items-center gap-1 rounded-md text-[12px] text-text-tertiary hover:bg-bg-tertiary cursor-pointer transition-colors">
              <Clock className="w-3.5 h-3.5" />Snooze
            </button>
            <button onClick={() => dismiss.mutate(item.id)} title="Dismiss"
              className="h-6 px-2 flex items-center gap-1 rounded-md text-[12px] text-text-quaternary hover:text-red hover:bg-bg-tertiary cursor-pointer transition-colors">
              <X className="w-3.5 h-3.5" />Dismiss
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/** Board form: a first-class Inbox lane on the left of the kanban. */
export function InboxLane({ items, projects = [] }: { items: InboxItem[]; projects?: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex-1 min-w-[240px] max-w-[320px] flex flex-col min-h-0">
      <div className="flex items-center gap-2 px-2 pb-2 shrink-0">
        <InboxIcon className="w-3.5 h-3.5 text-purple" />
        <span className="text-[13px] font-[510] text-text-tertiary">Inbox</span>
        <span className="text-[12px] font-mono text-text-quaternary">{items.length}</span>
      </div>
      <div className="flex-1 overflow-y-auto space-y-1.5 px-1">
        {items.map(it => <InboxItemCard key={it.id} item={it} projects={projects} />)}
      </div>
    </div>
  );
}

/** List/stream form: a collapsible Inbox section at the top. */
export function InboxSection({ items, projects = [], defaultOpen = true }: {
  items: InboxItem[]; projects?: string[]; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (items.length === 0) return null;
  return (
    <div className="mb-3">
      <button onClick={() => setOpen(!open)}
        className="flex items-center gap-2.5 w-full h-9 px-3 rounded-lg cursor-pointer hover:bg-bg-secondary transition-colors duration-75 text-left">
        <ChevronRight className={`w-3.5 h-3.5 text-text-quaternary transition-transform ${open ? 'rotate-90' : ''}`} />
        <InboxIcon className="w-3.5 h-3.5 text-purple" />
        <span className="text-[14px] font-[510] text-text-tertiary">Inbox</span>
        <span className="text-[13px] font-mono text-text-quaternary">{items.length}</span>
        <span className="text-[12px] text-text-quaternary opacity-50 ml-1">triage to promote</span>
      </button>
      {open && (
        <div className="pl-3 ml-3 border-l border-border space-y-1.5 mt-1">
          {items.map(it => <InboxItemCard key={it.id} item={it} projects={projects} />)}
        </div>
      )}
    </div>
  );
}

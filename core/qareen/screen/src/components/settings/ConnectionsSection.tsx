import { useState, useCallback, useRef, useEffect } from 'react';
import {
  Link2, ChevronDown, ChevronRight, MoreHorizontal,
  Github, Slack, Apple, Mail, Ship, CreditCard, Globe,
  Bot, Send, MessageCircle, Landmark, BarChart3, Database,
  type LucideIcon,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { SettingCard, LoadingRows } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// Connections — clean row per account, inspired by Claude's Connectors UI.
// Icon + name + identifier on left, status + menu on right.
// ---------------------------------------------------------------------------

interface AccountEntry {
  provider: string;
  identifier: string;
  label: string;
  trust: string;
  services: string[];
  note: string | null;
}

interface Context {
  id: string;
  label: string;
  description: string;
  default: boolean;
  accounts: AccountEntry[];
}

interface ConnectionsData {
  contexts: Context[];
  total_accounts: number;
}

function useConnections() {
  return useQuery({
    queryKey: ['connections'],
    queryFn: async (): Promise<ConnectionsData> => {
      const res = await fetch('/api/config/connections');
      if (!res.ok) throw new Error(`Connections API error: ${res.status}`);
      return res.json();
    },
    staleTime: 300_000,
    refetchOnWindowFocus: true,
  });
}

// Provider icons + brand colors
const PROVIDERS: Record<string, { icon: LucideIcon; color: string }> = {
  google:    { icon: Mail,          color: '#EA4335' },
  github:    { icon: Github,        color: '#8B949E' },
  telegram:  { icon: Send,          color: '#26A5E4' },
  slack:     { icon: Slack,         color: '#E01E5A' },
  apple_dev: { icon: Apple,         color: '#A2AAAD' },
  paypal:    { icon: CreditCard,    color: '#00457C' },
  wave:      { icon: Landmark,      color: '#1C6BFF' },
  chitchats: { icon: Ship,          color: '#FF6B35' },
  clickup:   { icon: BarChart3,     color: '#7B68EE' },
  plane:     { icon: BarChart3,     color: '#3F76FF' },
  openrouter:{ icon: Bot,           color: '#BF5AF2' },
  obsidian:  { icon: Database,      color: '#7C3AED' },
  whatsapp:  { icon: MessageCircle, color: '#25D366' },
};

const TRUST_LABELS: Record<string, { text: string; color: string }> = {
  open:  { text: 'Open',    color: '#30D158' },
  ask:   { text: 'Ask',     color: '#FFD60A' },
  never: { text: 'Never',   color: '#FF453A' },
};

/* ── Detail popover (three-dot menu) ── */

function DetailMenu({ account }: { account: AccountEntry }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="
          w-7 h-7 flex items-center justify-center rounded-[5px]
          text-text-quaternary hover:text-text-tertiary hover:bg-hover
          transition-colors duration-100 cursor-pointer
        "
      >
        <MoreHorizontal className="w-4 h-4" />
      </button>
      {open && (
        <div className="
          absolute right-0 top-full mt-1 z-50
          w-[180px] py-1
          bg-bg-panel border border-border rounded-[7px]
          shadow-[0_4px_16px_rgba(0,0,0,0.3)]
        ">
          {account.label && (
            <div className="px-3 py-1.5 text-[11px] text-text-quaternary border-b border-border">
              {account.label}
            </div>
          )}
          {account.services.length > 0 && (
            <div className="px-3 py-2 border-b border-border">
              <span className="text-[10px] text-text-quaternary block mb-1">Services</span>
              <div className="flex flex-wrap gap-1">
                {account.services.map((s) => (
                  <span key={s} className="text-[10px] text-text-tertiary bg-bg-tertiary px-1.5 py-0.5 rounded-[3px]">
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}
          {account.note && (
            <div className="px-3 py-1.5 text-[11px] text-text-quaternary italic">
              {account.note}
            </div>
          )}
          {!account.label && account.services.length === 0 && !account.note && (
            <div className="px-3 py-1.5 text-[11px] text-text-quaternary">
              No additional details
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Account row — icon + name + identifier | status + menu ── */

function AccountRow({ account }: { account: AccountEntry }) {
  const provider = PROVIDERS[account.provider] ?? { icon: Globe, color: '#9A9490' };
  const Icon = provider.icon;
  const trust = TRUST_LABELS[account.trust] ?? TRUST_LABELS.open;

  return (
    <div className="flex items-center gap-3 py-3 min-h-[48px]">
      {/* Icon */}
      <div
        className="w-8 h-8 rounded-[7px] flex items-center justify-center shrink-0"
        style={{ backgroundColor: `${provider.color}18` }}
      >
        <Icon className="w-4 h-4" style={{ color: provider.color }} />
      </div>

      {/* Name + identifier */}
      <div className="flex-1 min-w-0">
        <span className="text-[13px] font-[510] text-text-secondary block capitalize">
          {account.provider.replace(/_/g, ' ')}
        </span>
        <span className="text-[11px] text-text-quaternary block truncate">
          {account.identifier}
        </span>
      </div>

      {/* Status */}
      <span className="text-[12px] font-[510] shrink-0" style={{ color: trust.color }}>
        {trust.text}
      </span>

      {/* Menu */}
      <DetailMenu account={account} />
    </div>
  );
}

/* ── Grouped provider row — collapses multiple accounts under one provider ── */

function ProviderGroup({ provider: providerKey, accounts }: { provider: string; accounts: AccountEntry[] }) {
  const [expanded, setExpanded] = useState(false);
  const prov = PROVIDERS[providerKey] ?? { icon: Globe, color: '#9A9490' };
  const Icon = prov.icon;

  // Single account — render flat
  if (accounts.length === 1) {
    return <AccountRow account={accounts[0]} />;
  }

  // Multiple — collapsible group
  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 py-3 min-h-[48px] cursor-pointer"
      >
        <div
          className="w-8 h-8 rounded-[7px] flex items-center justify-center shrink-0"
          style={{ backgroundColor: `${prov.color}18` }}
        >
          <Icon className="w-4 h-4" style={{ color: prov.color }} />
        </div>
        <div className="flex-1 min-w-0 text-left">
          <span className="text-[13px] font-[510] text-text-secondary block capitalize">
            {providerKey.replace(/_/g, ' ')}
          </span>
          <span className="text-[11px] text-text-quaternary block">
            {accounts.length} accounts
          </span>
        </div>
        <ChevronDown
          className={`w-3.5 h-3.5 text-text-quaternary shrink-0 transition-transform duration-150 ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      {expanded && (
        <div className="ml-11 divide-y divide-border">
          {accounts.map((acc) => (
            <div key={acc.identifier} className="flex items-center gap-3 py-2.5 min-h-[40px]">
              <div className="flex-1 min-w-0">
                <span className="text-[12px] text-text-secondary block truncate">
                  {acc.identifier}
                </span>
                {acc.label && (
                  <span className="text-[11px] text-text-quaternary block">
                    {acc.label}
                  </span>
                )}
              </div>
              <span
                className="text-[11px] font-[510] shrink-0"
                style={{ color: (TRUST_LABELS[acc.trust] ?? TRUST_LABELS.open).color }}
              >
                {(TRUST_LABELS[acc.trust] ?? TRUST_LABELS.open).text}
              </span>
              <DetailMenu account={acc} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Group accounts by provider ── */

function groupByProvider(accounts: AccountEntry[]): { provider: string; accounts: AccountEntry[] }[] {
  const map = new Map<string, AccountEntry[]>();
  for (const acc of accounts) {
    const list = map.get(acc.provider) ?? [];
    list.push(acc);
    map.set(acc.provider, list);
  }
  return Array.from(map.entries()).map(([provider, accs]) => ({ provider, accounts: accs }));
}

/* ── Context filter pills ── */

function ContextFilter({
  contexts,
  active,
  onChange,
}: {
  contexts: Context[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex items-center gap-1 overflow-x-auto no-scrollbar py-3">
      <button
        onClick={() => onChange('all')}
        className={`
          shrink-0 px-2.5 py-1 rounded-full text-[11px] font-[510] cursor-pointer
          transition-all duration-150
          ${active === 'all'
            ? 'bg-[rgba(255,245,235,0.12)] text-text-secondary'
            : 'text-text-quaternary hover:text-text-tertiary hover:bg-[rgba(255,245,235,0.05)]'
          }
        `}
      >
        All
      </button>
      {contexts.map((ctx) => (
        <button
          key={ctx.id}
          onClick={() => onChange(ctx.id)}
          className={`
            shrink-0 px-2.5 py-1 rounded-full text-[11px] font-[510] cursor-pointer
            transition-all duration-150
            ${active === ctx.id
              ? 'bg-[rgba(255,245,235,0.12)] text-text-secondary'
              : 'text-text-quaternary hover:text-text-tertiary hover:bg-[rgba(255,245,235,0.05)]'
            }
          `}
        >
          {ctx.label.split('(')[0].trim()}
        </button>
      ))}
    </div>
  );
}

/* ── Main content ── */

function ConnectionsContent() {
  return (
    <SettingCard icon={Link2} title="Connections">
      <div className="py-2">
        <span className="text-[13px] text-text-secondary block mb-1">
          Providers, connectors, and credentials
        </span>
        <span className="text-[11px] text-text-quaternary block mb-3">
          Manage AI providers, MCP servers, API keys, and connected accounts.
        </span>
        <a
          href="/integrations"
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md bg-accent/10 hover:bg-accent/20 text-[12px] font-[510] text-accent transition-colors cursor-pointer"
          style={{ transitionDuration: '80ms' }}
        >
          Open Integrations <ChevronRight className="w-3 h-3" />
        </a>
      </div>
    </SettingCard>
  );
}

export const connectionsSection: SettingsSection = {
  id: 'connections',
  title: 'Connections',
  icon: Link2,
  component: ConnectionsContent,
};

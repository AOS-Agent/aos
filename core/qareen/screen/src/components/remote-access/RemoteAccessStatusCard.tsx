import { useState } from 'react';
import {
  Globe,
  ExternalLink,
  Mail,
  Loader2,
  AlertTriangle,
  ShieldCheck,
} from 'lucide-react';
import { SettingCard, SettingRow } from '../settings/shared';
import { StatusDot, type StatusDotColor } from '@/components/primitives';
import {
  useRemoteAccessStatus,
  useDisconnect,
  type RAConnectorHealth,
} from '@/hooks/useRemoteAccess';

// ---------------------------------------------------------------------------
// RemoteAccessStatusCard — the connected-state dashboard. Shows the live
// hostname, the allow-listed emails, per-component connector health, a Test
// button (opens the hostname), and a destructive Disconnect that restores LAN
// access.
// ---------------------------------------------------------------------------

// Contract #1: connector_health is string-valued — tunnel/dns/access are
// 'ok' | 'down', overall is 'ok' | 'degraded'. Map 'ok' → green, 'down' → red,
// anything else (incl. 'degraded'/missing) → gray. Coerce via String() so a
// stray boolean/undefined from the wire can never crash render (.toLowerCase
// on a boolean would throw).
const HEALTH_COLORS: Record<string, StatusDotColor> = {
  ok: 'green',
  down: 'red',
};

function healthColor(value: unknown): StatusDotColor {
  return HEALTH_COLORS[String(value ?? '').toLowerCase()] ?? 'gray';
}

function HealthChip({ label, value }: { label: string; value: unknown }) {
  const color = healthColor(value);
  return (
    <div className="flex items-center gap-2 h-7 px-2.5 rounded-full bg-bg-tertiary border border-border">
      <StatusDot color={color} pulse={color === 'yellow'} />
      <span className="text-[11px] font-[510] text-text-secondary">{label}</span>
    </div>
  );
}

/* ── Disconnect confirmation ── */

function DisconnectConfirm() {
  const [confirming, setConfirming] = useState(false);
  const disconnect = useDisconnect();

  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="
          inline-flex items-center gap-1.5 h-8 px-3 rounded-[5px]
          text-[12px] font-[510] text-red
          border border-red/30
          transition-colors duration-100 cursor-pointer
          hover:bg-red-muted
        "
      >
        Disconnect
      </button>
    );
  }

  return (
    <div className="space-y-3 px-3 py-3 rounded-[6px] bg-red-muted border border-red/20">
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-3.5 h-3.5 text-red mt-0.5 shrink-0" />
        <span className="text-[12px] text-text-secondary leading-relaxed">
          This tears down the Cloudflare tunnel, DNS record, and Access app,
          deletes the stored tokens, and rebinds Qareen to{' '}
          <span className="font-mono">0.0.0.0</span> — LAN and Tailscale access
          are restored.
        </span>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => disconnect.mutate()}
          disabled={disconnect.isPending}
          className="
            inline-flex items-center gap-1.5 h-8 px-3 rounded-[5px]
            text-[12px] font-[590] text-white bg-red
            transition-opacity duration-100 cursor-pointer
            hover:opacity-90
            disabled:opacity-50 disabled:pointer-events-none
          "
        >
          {disconnect.isPending ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Disconnecting…
            </>
          ) : (
            'Yes, disconnect'
          )}
        </button>
        <button
          type="button"
          onClick={() => setConfirming(false)}
          disabled={disconnect.isPending}
          className="
            inline-flex items-center h-8 px-3 rounded-[5px]
            text-[12px] font-[510] text-text-tertiary
            transition-colors duration-100 cursor-pointer
            hover:text-text-secondary hover:bg-hover
            disabled:opacity-40 disabled:pointer-events-none
          "
        >
          Cancel
        </button>
      </div>
      {disconnect.isError && (
        <p className="text-[11px] text-red">
          Disconnect failed. Check the connector logs and try again.
        </p>
      )}
    </div>
  );
}

export function RemoteAccessStatusCard() {
  const { data: status } = useRemoteAccessStatus();

  const hostname = status?.hostname ?? '';
  const emails = status?.allowed_emails ?? [];
  const health: RAConnectorHealth | null = status?.connector_health ?? null;
  const url = hostname ? `https://${hostname}` : '';

  const overallColor = healthColor(health?.overall ?? 'ok');

  return (
    <SettingCard
      icon={Globe}
      title="Remote Access"
      action={
        <span className="inline-flex items-center gap-1.5">
          <StatusDot color={overallColor} pulse={overallColor === 'yellow'} />
          <span
            className={`text-[11px] font-[510] ${
              overallColor === 'green'
                ? 'text-green'
                : overallColor === 'yellow'
                ? 'text-yellow'
                : overallColor === 'red'
                ? 'text-red'
                : 'text-text-quaternary'
            }`}
          >
            {overallColor === 'green' ? 'Connected' : 'Degraded'}
          </span>
        </span>
      }
    >
      {/* Hostname */}
      <SettingRow
        label="Address"
        trailing={
          url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="
                inline-flex items-center gap-1.5
                text-[13px] font-[510] text-accent font-mono
                hover:underline
              "
            >
              {hostname}
              <ExternalLink className="w-3 h-3" />
            </a>
          ) : undefined
        }
      />

      {/* Connector health */}
      <div className="flex items-center justify-between py-3 min-h-[44px]">
        <div className="flex-1 min-w-0 pr-4">
          <span className="text-[13px] font-[510] text-text-secondary block">
            Connector health
          </span>
          <span className="text-[12px] text-text-quaternary block mt-0.5">
            Tunnel, DNS, and Cloudflare Access
          </span>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5 shrink-0">
          <HealthChip label="Tunnel" value={health?.tunnel ?? 'unknown'} />
          <HealthChip label="DNS" value={health?.dns ?? 'unknown'} />
          <HealthChip label="Access" value={health?.access ?? 'unknown'} />
        </div>
      </div>

      {/* Allowed emails */}
      <div className="py-3">
        <div className="flex items-center gap-2 mb-2">
          <ShieldCheck className="w-3.5 h-3.5 text-text-quaternary" />
          <span className="text-[13px] font-[510] text-text-secondary">
            Allowed sign-ins
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {emails.length === 0 && (
            <span className="text-[12px] text-text-quaternary">None configured.</span>
          )}
          {emails.map((email) => (
            <span
              key={email}
              className="
                inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full
                bg-accent-subtle border border-accent/20
                text-[12px] text-text-secondary
              "
            >
              <Mail className="w-3 h-3 text-accent" />
              {email}
            </span>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between gap-3 py-3">
        <button
          type="button"
          onClick={() =>
            url && window.open(url, '_blank', 'noopener,noreferrer')
          }
          disabled={!url}
          className="
            inline-flex items-center gap-1.5 h-8 px-3.5 rounded-[5px]
            text-[12px] font-[590] text-on-accent bg-accent
            transition-colors duration-100 cursor-pointer
            hover:bg-accent-hover
            disabled:opacity-40 disabled:pointer-events-none
          "
        >
          Test <ExternalLink className="w-3.5 h-3.5" />
        </button>
        <DisconnectConfirm />
      </div>
    </SettingCard>
  );
}

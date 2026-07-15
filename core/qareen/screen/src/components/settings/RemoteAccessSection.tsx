import { useState } from 'react';
import { Globe, AlertTriangle, Loader2, RotateCw } from 'lucide-react';
import { SettingCard, LoadingRows } from './shared';
import { StatusDot } from '@/components/primitives';
import type { SettingsSection } from './types';
import { useRemoteAccessStatus, useDisconnect } from '@/hooks/useRemoteAccess';
import { RemoteAccessWizard } from '../remote-access/RemoteAccessWizard';
import { RemoteAccessStatusCard } from '../remote-access/RemoteAccessStatusCard';

// ---------------------------------------------------------------------------
// Remote Access — reach this Qareen from anywhere at aos.<domain>, gated by
// Cloudflare Access email one-time-PIN.
//
// Branches on the live status:
//   disconnected → setup wizard from step 1
//   provisioning → wizard parked on the live-progress step
//   connected    → status dashboard
//   error        → error card: surfaces the failure and exposes Disconnect
//                  (tears down any half-provisioned Cloudflare resources) plus
//                  Retry (restarts the wizard) — NOT a silent drop to step 1.
// Graceful when the backend isn't wired yet: status simply reads disconnected.
// ---------------------------------------------------------------------------

function RemoteAccessContent() {
  const { data, isLoading } = useRemoteAccessStatus();

  if (isLoading) {
    return (
      <SettingCard icon={Globe} title="Remote Access">
        <LoadingRows count={3} />
      </SettingCard>
    );
  }

  const status = data?.status ?? 'disconnected';

  if (status === 'connected') return <RemoteAccessStatusCard />;
  if (status === 'provisioning') return <RemoteAccessWizard initialStep={4} />;
  if (status === 'error')
    return <RemoteAccessErrorCard message={data?.error_message ?? null} />;
  return <RemoteAccessWizard />;
}

// ---------------------------------------------------------------------------
// RemoteAccessErrorCard — provisioning failed. Surfaces the backend
// error_message and the two safe escape hatches: Disconnect (POST /disconnect
// via useDisconnect — tears down any partially-created tunnel/DNS/Access
// resources and rebinds Qareen to 0.0.0.0) and Retry (restarts the wizard from
// the top). Previously an 'error' status fell through to the step-1 wizard,
// where no Disconnect control was reachable, orphaning half-provisioned cloud
// resources. Disconnect is fire-and-forget on the backend (202); the status
// poll re-flips this card to the wizard once teardown settles to 'disconnected'.
// ---------------------------------------------------------------------------

function RemoteAccessErrorCard({ message }: { message: string | null }) {
  const [retrying, setRetrying] = useState(false);
  const disconnect = useDisconnect();

  // Retry just restarts the wizard from step 1 (fresh provisioning attempt).
  if (retrying) return <RemoteAccessWizard />;

  return (
    <SettingCard
      icon={Globe}
      title="Remote Access"
      action={
        <span className="inline-flex items-center gap-1.5">
          <StatusDot color="red" />
          <span className="text-[11px] font-[510] text-red">Error</span>
        </span>
      }
    >
      {/* Failure detail */}
      <div className="py-3">
        <div className="flex items-start gap-2 px-3 py-3 rounded-[6px] bg-red-muted border border-red/20">
          <AlertTriangle className="w-3.5 h-3.5 text-red mt-0.5 shrink-0" />
          <div className="min-w-0">
            <span className="text-[13px] font-[510] text-text-secondary block">
              Setup failed
            </span>
            <span className="text-[12px] text-text-secondary leading-relaxed block mt-0.5 break-words">
              {message ||
                'Provisioning stopped before it finished. Disconnect to tear down anything that was partially created, then try again.'}
            </span>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between gap-3 py-3">
        <button
          type="button"
          onClick={() => setRetrying(true)}
          disabled={disconnect.isPending}
          className="
            inline-flex items-center gap-1.5 h-8 px-3 rounded-[5px]
            text-[12px] font-[510] text-text-secondary
            border border-border
            transition-colors duration-100 cursor-pointer
            hover:bg-hover
            disabled:opacity-40 disabled:pointer-events-none
          "
        >
          <RotateCw className="w-3.5 h-3.5" /> Retry
        </button>
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
            'Disconnect & reset'
          )}
        </button>
      </div>

      {disconnect.isError && (
        <p className="text-[11px] text-red pb-3">
          Disconnect failed. Check the connector logs and try again.
        </p>
      )}
    </SettingCard>
  );
}

export const remoteAccessSection: SettingsSection = {
  id: 'remote-access',
  title: 'Remote Access',
  icon: Globe,
  component: RemoteAccessContent,
};

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ExternalLink,
  Check,
  X,
  Loader2,
  AlertTriangle,
  ShieldCheck,
  Globe,
  Mail,
  Plus,
  ArrowLeft,
  ArrowRight,
  Lock,
} from 'lucide-react';
import { SettingCard } from '../settings/shared';
import { useOperator } from '@/hooks/useConfig';
import {
  useValidateToken,
  useConnect,
  useRemoteAccessProgress,
  useRemoteAccessStatus,
  type ValidateTokenResult,
  type ConnectRequest,
  type RAProgress,
  type RAZone,
} from '@/hooks/useRemoteAccess';
import { buildCfTokenDeepLink } from '@/lib/cfDeepLink';

// ---------------------------------------------------------------------------
// RemoteAccessWizard — links a Cloudflare-hosted domain so this Qareen is
// reachable at aos.<domain>, gated by Cloudflare Access email one-time-PIN.
//
//   Step 1 · Connect   create + paste a scoped Cloudflare API token
//   Step 2 · Hostname  pick the zone + subdomain (defaults to aos.<domain>)
//   Step 3 · Access    allow-list the emails that may sign in
//   Step 4 · Go        provision, streaming live progress over SSE
//
// Enabling remote access closes the LAN / Tailscale door (Qareen rebinds to
// 127.0.0.1) so Cloudflare Access becomes the only way in.
// ---------------------------------------------------------------------------

const STEP_LABELS = ['Connect', 'Hostname', 'Access', 'Go'] as const;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SUBDOMAIN_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/i;

// Friendly names for the permission-group keys the deep-link asks for.
const SCOPE_LABELS: Record<string, string> = {
  argotunnel: 'Cloudflare Tunnel · Edit',
  dns: 'DNS · Edit',
  zone: 'Zone · Read',
  access: 'Access: Apps & Policies · Edit',
  access_acct: 'Access: Orgs, IdP & Groups · Edit',
};

// Keyed by the real step ids emitted by the backend TunnelManager._emit.
// The backend always sends a non-empty message, so this map is a fallback
// safety net (used only if a future emit ships an empty message).
const PROVISION_STEP_LABELS: Record<string, string> = {
  // provision flow
  start: 'Starting',
  tunnel: 'Creating tunnel',
  ingress: 'Configuring ingress',
  dns: 'Setting up DNS',
  idp: 'Preparing identity provider',
  access_app: 'Creating Access application',
  policy: 'Applying access policy',
  connector: 'Deploying connector',
  health: 'Verifying health',
  rebind: 'Securing local binding',
  complete: 'Done',
  error: 'Failed',
  // disconnect flow
  cloudflare: 'Cleaning up Cloudflare',
  secrets: 'Clearing credentials',
};

/* ── Step header ── */

function StepHeader({ current }: { current: number }) {
  return (
    <div className="flex items-center gap-1.5 mb-5">
      {STEP_LABELS.map((label, i) => {
        const n = i + 1;
        const done = n < current;
        const active = n === current;
        return (
          <div key={label} className="flex items-center gap-1.5">
            <div className="flex items-center gap-1.5">
              <span
                className={`
                  flex items-center justify-center w-[18px] h-[18px] rounded-full text-[10px] font-[590]
                  transition-colors duration-150
                  ${active ? 'bg-accent text-white'
                    : done ? 'bg-accent-subtle text-accent'
                    : 'bg-bg-tertiary text-text-quaternary'}
                `}
              >
                {done ? <Check className="w-2.5 h-2.5" /> : n}
              </span>
              <span
                className={`text-[11px] font-[510] ${
                  active ? 'text-text-secondary' : 'text-text-quaternary'
                }`}
              >
                {label}
              </span>
            </div>
            {n < STEP_LABELS.length && (
              <span className="w-4 h-px bg-border mx-0.5" />
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── Footer (Back / Next) ── */

function WizardFooter({
  step,
  canNext,
  onBack,
  onNext,
  nextLabel = 'Continue',
}: {
  step: number;
  canNext: boolean;
  onBack: () => void;
  onNext: () => void;
  nextLabel?: string;
}) {
  return (
    <div className="flex items-center justify-between pt-5 mt-1 border-t border-border">
      <button
        type="button"
        onClick={onBack}
        disabled={step === 1}
        className="
          inline-flex items-center gap-1.5 h-8 px-3 rounded-[5px]
          text-[12px] font-[510] text-text-tertiary
          transition-colors duration-100 cursor-pointer
          hover:text-text-secondary hover:bg-hover
          disabled:opacity-30 disabled:pointer-events-none
        "
      >
        <ArrowLeft className="w-3.5 h-3.5" /> Back
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={!canNext}
        className="
          inline-flex items-center gap-1.5 h-8 px-3.5 rounded-[5px]
          text-[12px] font-[590] text-white bg-accent
          transition-colors duration-100 cursor-pointer
          hover:bg-accent-hover
          disabled:opacity-40 disabled:pointer-events-none
        "
      >
        {nextLabel} <ArrowRight className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

/* ── Step 1 · Connect ── */

function StepConnect({
  token,
  setToken,
  validation,
  setValidation,
}: {
  token: string;
  setToken: (v: string) => void;
  validation: ValidateTokenResult | null;
  setValidation: (v: ValidateTokenResult | null) => void;
}) {
  const validate = useValidateToken();

  const runValidate = useCallback(
    async (value: string) => {
      const t = value.trim();
      if (!t) return;
      setValidation(null);
      try {
        const result = await validate.mutateAsync(t);
        setValidation(result);
      } catch (e) {
        setValidation({
          ok: false,
          error: e instanceof Error ? e.message : 'Validation failed',
        });
      }
    },
    [validate, setValidation],
  );

  return (
    <div className="space-y-4">
      <p className="text-[12px] text-text-tertiary leading-relaxed">
        Qareen needs a scoped Cloudflare API token for a domain already on
        Cloudflare. Create one with the exact permissions pre-filled, then paste
        it below — it is stored only in your macOS Keychain, never on disk.
      </p>

      <button
        type="button"
        onClick={() =>
          window.open(buildCfTokenDeepLink(), '_blank', 'noopener,noreferrer')
        }
        className="
          inline-flex items-center gap-1.5 h-8 px-3.5 rounded-[5px]
          text-[12px] font-[590] text-white bg-accent
          transition-colors duration-100 cursor-pointer
          hover:bg-accent-hover
        "
      >
        Create token on Cloudflare <ExternalLink className="w-3.5 h-3.5" />
      </button>

      <div>
        <label
          htmlFor="cf-token"
          className="text-[11px] font-[510] text-text-quaternary block mb-1.5"
        >
          Paste your token
        </label>
        <div className="flex items-center gap-2">
          <input
            id="cf-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => {
              setToken(e.target.value);
              if (validation) setValidation(null);
            }}
            onBlur={(e) => runValidate(e.target.value)}
            onPaste={(e) => {
              const pasted = e.clipboardData.getData('text');
              if (pasted) {
                setToken(pasted);
                // Validate on the pasted value directly (state isn't synced yet).
                setTimeout(() => runValidate(pasted), 0);
                e.preventDefault();
              }
            }}
            placeholder="v1.0-..."
            className="
              flex-1 h-8 px-2.5 rounded-[5px]
              bg-bg-tertiary border border-border font-mono
              text-[12px] text-text-secondary
              placeholder:text-text-quaternary placeholder:font-sans
              transition-colors duration-100
              hover:border-border-secondary
              focus:outline-none focus:border-accent/60
            "
          />
          <button
            type="button"
            onClick={() => runValidate(token)}
            disabled={!token.trim() || validate.isPending}
            className="
              shrink-0 inline-flex items-center gap-1.5 h-8 px-3 rounded-[5px]
              text-[12px] font-[510] text-text-secondary
              bg-bg-tertiary border border-border-secondary
              transition-colors duration-100 cursor-pointer
              hover:bg-hover hover:text-text
              disabled:opacity-40 disabled:pointer-events-none
            "
          >
            {validate.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              'Verify'
            )}
          </button>
        </div>
      </div>

      {/* Result */}
      {validate.isPending && (
        <div className="flex items-center gap-2 text-[12px] text-text-tertiary">
          <Loader2 className="w-3.5 h-3.5 text-accent animate-spin" />
          Checking token and listing zones…
        </div>
      )}

      {!validate.isPending && validation?.ok && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-[6px] bg-green-muted border border-green/20">
          <Check className="w-3.5 h-3.5 text-green mt-0.5 shrink-0" />
          <span className="text-[12px] text-text-secondary">
            Token verified ·{' '}
            <span className="text-text">
              {validation.zones?.length ?? 0} domain
              {(validation.zones?.length ?? 0) === 1 ? '' : 's'}
            </span>{' '}
            available.
          </span>
        </div>
      )}

      {!validate.isPending &&
        validation &&
        !validation.ok &&
        (validation.missing_scopes?.length ? (
          <div className="px-3 py-2.5 rounded-[6px] bg-yellow-muted border border-yellow/20">
            <div className="flex items-center gap-2 mb-1.5">
              <AlertTriangle className="w-3.5 h-3.5 text-yellow shrink-0" />
              <span className="text-[12px] font-[510] text-text-secondary">
                This token is missing some permissions
              </span>
            </div>
            <ul className="space-y-0.5 pl-5">
              {validation.missing_scopes.map((s) => (
                <li
                  key={s}
                  className="text-[11px] text-text-tertiary list-disc marker:text-yellow"
                >
                  {SCOPE_LABELS[s] ?? s}
                </li>
              ))}
            </ul>
            <p className="text-[11px] text-text-quaternary mt-2 pl-5">
              Re-create the token with the link above (all rows pre-fill) and
              paste it again.
            </p>
          </div>
        ) : (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-[6px] bg-red-muted border border-red/20">
            <X className="w-3.5 h-3.5 text-red mt-0.5 shrink-0" />
            <span className="text-[12px] text-text-secondary">
              {validation.error ?? 'Token could not be verified.'}
            </span>
          </div>
        ))}
    </div>
  );
}

/* ── Step 2 · Hostname ── */

function StepHostname({
  zones,
  zoneId,
  setZoneId,
  subdomain,
  setSubdomain,
}: {
  zones: RAZone[];
  zoneId: string;
  setZoneId: (v: string) => void;
  subdomain: string;
  setSubdomain: (v: string) => void;
}) {
  const domain = zones.find((z) => z.id === zoneId)?.name ?? '';
  const sub = subdomain.trim();
  const subValid = SUBDOMAIN_RE.test(sub);
  const hostname = subValid && domain ? `${sub}.${domain}` : '';

  return (
    <div className="space-y-4">
      <p className="text-[12px] text-text-tertiary leading-relaxed">
        Choose the public address for this machine. Apex domains aren't
        supported — Qareen lives on a subdomain.
      </p>

      <div>
        <span className="text-[11px] font-[510] text-text-quaternary block mb-1.5">
          Domain
        </span>
        <div className="space-y-1">
          {zones.map((z) => {
            const selected = z.id === zoneId;
            return (
              <button
                key={z.id}
                type="button"
                onClick={() => setZoneId(z.id)}
                className={`
                  w-full flex items-center justify-between px-3 h-9 rounded-[6px]
                  border transition-colors duration-100 cursor-pointer text-left
                  ${selected
                    ? 'bg-accent-subtle border-accent/40'
                    : 'bg-bg-tertiary border-border hover:border-border-secondary'}
                `}
              >
                <span className="flex items-center gap-2">
                  <Globe
                    className={`w-3.5 h-3.5 ${selected ? 'text-accent' : 'text-text-quaternary'}`}
                  />
                  <span className="text-[13px] text-text-secondary">{z.name}</span>
                </span>
                {selected && <Check className="w-3.5 h-3.5 text-accent" />}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <label
          htmlFor="ra-subdomain"
          className="text-[11px] font-[510] text-text-quaternary block mb-1.5"
        >
          Subdomain
        </label>
        <div className="flex items-center gap-2">
          <input
            id="ra-subdomain"
            type="text"
            value={subdomain}
            spellCheck={false}
            autoCapitalize="none"
            onChange={(e) =>
              setSubdomain(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))
            }
            placeholder="aos"
            className="
              w-32 h-8 px-2.5 rounded-[5px]
              bg-bg-tertiary border border-border font-mono
              text-[13px] text-text-secondary
              placeholder:text-text-quaternary
              transition-colors duration-100
              hover:border-border-secondary
              focus:outline-none focus:border-accent/60
            "
          />
          <span className="text-[13px] text-text-quaternary font-mono">
            .{domain || 'your-domain'}
          </span>
        </div>
        {!subValid && sub.length > 0 && (
          <p className="text-[11px] text-red mt-1.5">
            Use only letters, numbers, and hyphens.
          </p>
        )}
        {sub.length === 0 && (
          <p className="text-[11px] text-text-quaternary mt-1.5">
            A subdomain is required — the apex domain can't be used.
          </p>
        )}
      </div>

      {hostname && (
        <div className="flex items-center gap-2 px-3 py-2.5 rounded-[6px] bg-bg-tertiary border border-border">
          <span className="text-[11px] text-text-quaternary">Qareen will be at</span>
          <span className="text-[13px] font-[590] text-accent font-mono">
            {hostname}
          </span>
        </div>
      )}
    </div>
  );
}

/* ── Step 3 · Access (email chips) ── */

function EmailChips({
  emails,
  setEmails,
}: {
  emails: string[];
  setEmails: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);

  const add = useCallback(() => {
    const value = draft.trim().toLowerCase();
    if (!value) return;
    if (!EMAIL_RE.test(value)) {
      setError('Enter a valid email address.');
      return;
    }
    if (emails.includes(value)) {
      setError('That email is already allowed.');
      setDraft('');
      return;
    }
    setEmails([...emails, value]);
    setDraft('');
    setError(null);
  }, [draft, emails, setEmails]);

  const remove = useCallback(
    (email: string) => setEmails(emails.filter((e) => e !== email)),
    [emails, setEmails],
  );

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {emails.map((email) => (
          <span
            key={email}
            className="
              inline-flex items-center gap-1.5 h-7 pl-2.5 pr-1.5 rounded-full
              bg-accent-subtle border border-accent/20
              text-[12px] text-text-secondary
            "
          >
            <Mail className="w-3 h-3 text-accent" />
            {email}
            <button
              type="button"
              onClick={() => remove(email)}
              aria-label={`Remove ${email}`}
              className="
                flex items-center justify-center w-4 h-4 rounded-full
                text-text-quaternary hover:text-text hover:bg-hover
                transition-colors duration-100 cursor-pointer
              "
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
        {emails.length === 0 && (
          <span className="text-[11px] text-text-quaternary py-1">
            No one can sign in yet — add at least one email.
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <input
          type="email"
          value={draft}
          spellCheck={false}
          autoCapitalize="none"
          onChange={(e) => {
            setDraft(e.target.value);
            if (error) setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault();
              add();
            }
          }}
          onBlur={add}
          placeholder="name@example.com"
          className="
            flex-1 h-8 px-2.5 rounded-[5px]
            bg-bg-tertiary border border-border
            text-[13px] text-text-secondary
            placeholder:text-text-quaternary
            transition-colors duration-100
            hover:border-border-secondary
            focus:outline-none focus:border-accent/60
          "
        />
        <button
          type="button"
          onClick={add}
          disabled={!draft.trim()}
          className="
            shrink-0 inline-flex items-center gap-1 h-8 px-3 rounded-[5px]
            text-[12px] font-[510] text-text-secondary
            bg-bg-tertiary border border-border-secondary
            transition-colors duration-100 cursor-pointer
            hover:bg-hover hover:text-text
            disabled:opacity-40 disabled:pointer-events-none
          "
        >
          <Plus className="w-3.5 h-3.5" /> Add
        </button>
      </div>
      {error && <p className="text-[11px] text-red mt-1.5">{error}</p>}
    </div>
  );
}

function StepAccess({
  emails,
  setEmails,
}: {
  emails: string[];
  setEmails: (v: string[]) => void;
}) {
  const { data: op } = useOperator();
  const seeded = useRef(false);

  // Seed the operator's own email once, so the wizard isn't empty.
  useEffect(() => {
    if (!seeded.current && emails.length === 0 && op?.email && EMAIL_RE.test(op.email)) {
      seeded.current = true;
      setEmails([op.email.toLowerCase()]);
    }
  }, [op?.email, emails.length, setEmails]);

  return (
    <div className="space-y-4">
      <p className="text-[12px] text-text-tertiary leading-relaxed">
        Only these email addresses can reach Qareen. Each receives a one-time PIN
        from Cloudflare to sign in — there's no password.
      </p>
      <EmailChips emails={emails} setEmails={setEmails} />
    </div>
  );
}

/* ── Step 4 · Go (live provisioning) ── */

function StepIcon({ status }: { status: RAProgress['status'] }) {
  if (status === 'done') return <Check className="w-3.5 h-3.5 text-green shrink-0" />;
  if (status === 'error') return <X className="w-3.5 h-3.5 text-red shrink-0" />;
  return <Loader2 className="w-3.5 h-3.5 text-accent animate-spin shrink-0" />;
}

function StepProvision({ request }: { request: ConnectRequest | null }) {
  const connect = useConnect();
  const { steps, ready, reset } = useRemoteAccessProgress();
  const { data: status } = useRemoteAccessStatus();
  const fired = useRef(false);

  useEffect(() => {
    // Only POST /connect once the SSE stream is open (ready===true), so the
    // backend's first progress events can't be emitted before we're subscribed
    // (closes the SSE-subscribe-vs-POST race). Single-fire ref guard retained.
    if (request && ready && !fired.current) {
      fired.current = true;
      reset();
      connect.mutate(request);
    }
    // connect/reset are stable enough; we intentionally fire exactly once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, ready]);

  const hasError = steps.some((s) => s.status === 'error') || connect.isError;
  const complete =
    steps.some((s) => s.step === 'complete' && s.status === 'done') ||
    status?.status === 'connected';

  return (
    <div className="space-y-4">
      {/* LAN/Tailscale warning */}
      <div className="flex items-start gap-2 px-3 py-2.5 rounded-[6px] bg-yellow-muted border border-yellow/20">
        <Lock className="w-3.5 h-3.5 text-yellow mt-0.5 shrink-0" />
        <span className="text-[12px] text-text-secondary leading-relaxed">
          Once connected, Qareen rebinds to <span className="font-mono">127.0.0.1</span>{' '}
          and is reachable <span className="text-text">only</span> through your
          hostname behind Cloudflare Access. Local LAN and Tailscale access close
          until you disconnect.
        </span>
      </div>

      {connect.isError && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-[6px] bg-red-muted border border-red/20">
          <X className="w-3.5 h-3.5 text-red mt-0.5 shrink-0" />
          <span className="text-[12px] text-text-secondary">
            {connect.error instanceof Error
              ? connect.error.message
              : 'Could not start provisioning.'}
          </span>
        </div>
      )}

      {/* Live step list */}
      <div className="space-y-0.5">
        {steps.length === 0 && !connect.isError && (
          <div className="flex items-center gap-2 py-2 text-[12px] text-text-tertiary">
            <Loader2 className="w-3.5 h-3.5 text-accent animate-spin" />
            {request ? 'Starting provisioning…' : 'Provisioning in progress…'}
          </div>
        )}
        {steps.map((s) => (
          <div
            key={s.step}
            className="flex items-start gap-2.5 py-1.5"
          >
            <span className="mt-0.5">
              <StepIcon status={s.status} />
            </span>
            <div className="min-w-0">
              <span
                className={`text-[12px] ${
                  s.status === 'error' ? 'text-red' : 'text-text-secondary'
                }`}
              >
                {s.message || PROVISION_STEP_LABELS[s.step] || s.step}
              </span>
              {s.detail && (
                <span className="block text-[11px] text-text-quaternary mt-0.5 truncate">
                  {s.detail}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      {complete && (
        <div className="flex items-center gap-2 px-3 py-2.5 rounded-[6px] bg-green-muted border border-green/20">
          <ShieldCheck className="w-3.5 h-3.5 text-green shrink-0" />
          <span className="text-[12px] text-text-secondary">
            Remote access is live. Loading your status…
          </span>
        </div>
      )}

      {hasError && !complete && (
        <p className="text-[11px] text-text-quaternary">
          Provisioning hit a problem. Review the steps above, then disconnect and
          try again from the status screen.
        </p>
      )}
    </div>
  );
}

/* ── Wizard shell ── */

export function RemoteAccessWizard({ initialStep = 1 }: { initialStep?: number }) {
  const [step, setStep] = useState(initialStep);
  const [token, setToken] = useState('');
  const [validation, setValidation] = useState<ValidateTokenResult | null>(null);
  const [zoneId, setZoneId] = useState('');
  const [subdomain, setSubdomain] = useState('aos');
  const [emails, setEmails] = useState<string[]>([]);

  const zones = useMemo(() => validation?.zones ?? [], [validation]);

  // Default the zone selection to the first available domain.
  useEffect(() => {
    if (zones.length && !zones.some((z) => z.id === zoneId)) {
      setZoneId(zones[0].id);
    }
  }, [zones, zoneId]);

  const domain = zones.find((z) => z.id === zoneId)?.name ?? '';
  const sub = subdomain.trim();
  const hostname = SUBDOMAIN_RE.test(sub) && domain ? `${sub}.${domain}` : '';

  const connectRequest: ConnectRequest | null =
    validation?.ok && validation.account_id && zoneId && hostname && emails.length > 0
      ? {
          token,
          domain,
          hostname,
          zone_id: zoneId,
          account_id: validation.account_id,
          allowed_emails: emails,
        }
      : null;

  const canNext =
    step === 1
      ? Boolean(validation?.ok)
      : step === 2
      ? Boolean(hostname)
      : step === 3
      ? emails.length > 0 && emails.every((e) => EMAIL_RE.test(e))
      : false;

  return (
    <SettingCard icon={Globe} title="Remote Access">
      <div className="py-4">
        <StepHeader current={step} />

        {step === 1 && (
          <StepConnect
            token={token}
            setToken={setToken}
            validation={validation}
            setValidation={setValidation}
          />
        )}
        {step === 2 && (
          <StepHostname
            zones={zones}
            zoneId={zoneId}
            setZoneId={setZoneId}
            subdomain={subdomain}
            setSubdomain={setSubdomain}
          />
        )}
        {step === 3 && <StepAccess emails={emails} setEmails={setEmails} />}
        {step === 4 && <StepProvision request={connectRequest} />}

        {step < 4 && (
          <WizardFooter
            step={step}
            canNext={canNext}
            onBack={() => setStep((s) => Math.max(1, s - 1))}
            onNext={() => setStep((s) => Math.min(4, s + 1))}
            nextLabel={step === 3 ? 'Enable remote access' : 'Continue'}
          />
        )}
      </div>
    </SettingCard>
  );
}

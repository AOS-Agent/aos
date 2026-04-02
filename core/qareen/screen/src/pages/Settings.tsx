import { useState, useCallback, useEffect } from 'react';
import {
  User, MapPin, Clock, Moon as MoonIcon, Sun, Compass,
  Wifi, WifiOff, Globe, Link2, Server,
  HardDrive, Database, RefreshCw, Info,
  ChevronRight, type LucideIcon,
} from 'lucide-react';
import { useOperator } from '@/hooks/useConfig';
import { useHealth } from '@/hooks/useHealth';
import { useServices } from '@/hooks/useServices';
import { useRealtimeStore } from '@/store/realtime';
import { StatusDot, Tag, Skeleton } from '@/components/primitives';
import { useQueryClient } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Settings — iOS-style organized configuration view
// ---------------------------------------------------------------------------

/* ── Shared row component ── */
function SettingRow({
  label,
  value,
  description,
  trailing,
}: {
  label: string;
  value?: string | React.ReactNode;
  description?: string;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between py-3 min-h-[44px]">
      <div className="flex-1 min-w-0 pr-4">
        <span className="text-[13px] font-[510] text-text-secondary block">{label}</span>
        {description && (
          <span className="text-[12px] text-text-quaternary block mt-0.5 font-serif">{description}</span>
        )}
      </div>
      {trailing ?? (
        <span className="text-[13px] text-text-tertiary shrink-0 text-right max-w-[50%] truncate font-serif">
          {value ?? '\u2014'}
        </span>
      )}
    </div>
  );
}

/* ── Section wrapper ── */
function SettingSection({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-8">
      <div className="flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4 text-text-quaternary" />
        <h2 className="text-[13px] font-[590] text-text tracking-[-0.01em]">{title}</h2>
      </div>
      <div className="bg-bg-secondary rounded-[7px] border border-border divide-y divide-border px-4">
        {children}
      </div>
    </section>
  );
}

/* ── Toggle switch ── */
function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`
        relative w-[42px] h-[26px] rounded-full cursor-pointer
        transition-colors duration-150
        ${checked ? 'bg-accent' : 'bg-bg-quaternary'}
      `}
    >
      <span
        className={`
          absolute top-[3px] w-[20px] h-[20px] rounded-full bg-white
          shadow-[0_1px_3px_rgba(0,0,0,0.3)]
          transition-transform duration-150
          ${checked ? 'translate-x-[19px]' : 'translate-x-[3px]'}
        `}
      />
    </button>
  );
}

/* ── Loading rows ── */
function LoadingRows({ count = 3 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex items-center justify-between py-3 min-h-[44px]">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-32" />
        </div>
      ))}
    </>
  );
}

/* ── Profile Section ── */
function ProfileSection() {
  const { data: op, isLoading } = useOperator();

  if (isLoading) return <SettingSection icon={User} title="Profile"><LoadingRows count={4} /></SettingSection>;

  const location = op?.preferences?.location;
  const city = location?.city ?? location?.name;
  const coords = location?.latitude && location?.longitude
    ? `${Number(location.latitude).toFixed(2)}, ${Number(location.longitude).toFixed(2)}`
    : null;

  return (
    <SettingSection icon={User} title="Profile">
      <SettingRow label="Name" value={op?.name} />
      {op?.handle && <SettingRow label="Handle" value={`@${op.handle}`} />}
      {op?.email && <SettingRow label="Email" value={op.email} />}
      {city && (
        <SettingRow
          label="Location"
          value={city}
          description={coords ?? undefined}
        />
      )}
      <SettingRow label="Timezone" value={op?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone} />
      {op?.locale && <SettingRow label="Locale" value={op.locale} />}
    </SettingSection>
  );
}

/* ── Prayer Section ── */
function PrayerSection() {
  const { data: op, isLoading } = useOperator();

  if (isLoading) return <SettingSection icon={Compass} title="Prayer"><LoadingRows count={2} /></SettingSection>;

  const prefs = op?.preferences ?? {};
  const method = prefs.prayer_method ?? prefs.calculation_method ?? 'ISNA';

  return (
    <SettingSection icon={Compass} title="Prayer">
      <SettingRow label="Calculation method" value={String(method).toUpperCase()} />
      {prefs.morning_briefing_time && (
        <SettingRow label="Morning briefing" value={prefs.morning_briefing_time} />
      )}
      {prefs.evening_checkin_time && (
        <SettingRow label="Evening check-in" value={prefs.evening_checkin_time} />
      )}
      {prefs.quiet_hours_start && prefs.quiet_hours_end && (
        <SettingRow
          label="Quiet hours"
          value={`${prefs.quiet_hours_start} \u2013 ${prefs.quiet_hours_end}`}
        />
      )}
    </SettingSection>
  );
}

/* ── Appearance Section ── */
function AppearanceSection() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [fontSize, setFontSize] = useState<'default' | 'large'>('default');

  useEffect(() => {
    const saved = localStorage.getItem('qareen-theme') as 'dark' | 'light' | null;
    if (saved) setTheme(saved);
    const savedSize = localStorage.getItem('qareen-font-size') as 'default' | 'large' | null;
    if (savedSize) setFontSize(savedSize);
  }, []);

  const toggleTheme = useCallback((isDark: boolean) => {
    const next = isDark ? 'dark' : 'light';
    setTheme(next);
    localStorage.setItem('qareen-theme', next);
    document.documentElement.setAttribute('data-theme', next);
    document.documentElement.classList.toggle('dark', next === 'dark');
  }, []);

  const toggleFontSize = useCallback((isLarge: boolean) => {
    const next = isLarge ? 'large' : 'default';
    setFontSize(next);
    localStorage.setItem('qareen-font-size', next);
    document.documentElement.style.fontSize = isLarge ? '16px' : '';
  }, []);

  return (
    <SettingSection icon={theme === 'dark' ? MoonIcon : Sun} title="Appearance">
      <SettingRow
        label="Dark mode"
        description="Warm dark browns for the qareen's canvas"
        trailing={<Toggle checked={theme === 'dark'} onChange={toggleTheme} />}
      />
      <SettingRow
        label="Larger text"
        description="Increase base font size for readability"
        trailing={<Toggle checked={fontSize === 'large'} onChange={toggleFontSize} />}
      />
    </SettingSection>
  );
}

/* ── Connections Section ── */
function ConnectionsSection() {
  const connected = useRealtimeStore((s) => s.connected);
  const { data: services, isLoading } = useServices();

  // Derive specific service statuses
  const tailscale = services?.find((s) => s.name?.toLowerCase().includes('tailscale'));
  const google = services?.find(
    (s) => s.name?.toLowerCase().includes('google') || s.name?.toLowerCase().includes('gws'),
  );

  return (
    <SettingSection icon={connected ? Wifi : WifiOff} title="Connections">
      <SettingRow
        label="SSE stream"
        description="Real-time event connection to the qareen"
        trailing={
          <StatusDot
            color={connected ? 'green' : 'red'}
            size="md"
            label={connected ? 'Connected' : 'Offline'}
          />
        }
      />
      {isLoading ? (
        <LoadingRows count={2} />
      ) : (
        <>
          <SettingRow
            label="Tailscale"
            trailing={
              tailscale ? (
                <StatusDot
                  color={tailscale.status === 'running' ? 'green' : 'gray'}
                  size="md"
                  label={tailscale.status === 'running' ? 'Connected' : 'Inactive'}
                />
              ) : (
                <span className="text-[12px] text-text-quaternary font-serif">Not detected</span>
              )
            }
          />
          <SettingRow
            label="Google Workspace"
            trailing={
              google ? (
                <StatusDot
                  color={google.status === 'running' ? 'green' : google.status === 'error' ? 'red' : 'gray'}
                  size="md"
                  label={google.status === 'running' ? 'Active' : google.status ?? 'Inactive'}
                />
              ) : (
                <span className="text-[12px] text-text-quaternary font-serif">Not configured</span>
              )
            }
          />
        </>
      )}
    </SettingSection>
  );
}

/* ── System Section ── */
function SystemSection() {
  const { data: health, isLoading: healthLoading } = useHealth();
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    await queryClient.invalidateQueries();
    setTimeout(() => setRefreshing(false), 800);
  }, [queryClient]);

  return (
    <SettingSection icon={Server} title="System">
      {healthLoading ? (
        <LoadingRows count={3} />
      ) : (
        <>
          <SettingRow
            label="Disk usage"
            description="Internal SSD"
            trailing={
              <div className="flex items-center gap-2">
                <div className="w-20 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      (health?.disk_pct ?? 0) > 90 ? 'bg-red' : (health?.disk_pct ?? 0) > 70 ? 'bg-yellow' : 'bg-green'
                    }`}
                    style={{ width: `${health?.disk_pct ?? 0}%` }}
                  />
                </div>
                <span className="text-[11px] font-mono text-text-tertiary w-10 text-right">
                  {health?.disk_pct ?? 0}%
                </span>
              </div>
            }
          />
          <SettingRow
            label="Free space"
            value={health?.disk_free_gb ? `${health.disk_free_gb.toFixed(1)} GB` : '\u2014'}
          />
          <SettingRow
            label="Memory"
            description="RAM usage"
            trailing={
              <div className="flex items-center gap-2">
                <div className="w-20 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      (health?.ram_pct ?? 0) > 90 ? 'bg-red' : (health?.ram_pct ?? 0) > 70 ? 'bg-yellow' : 'bg-green'
                    }`}
                    style={{ width: `${health?.ram_pct ?? 0}%` }}
                  />
                </div>
                <span className="text-[11px] font-mono text-text-tertiary w-10 text-right">
                  {health?.ram_pct ?? 0}%
                </span>
              </div>
            }
          />
        </>
      )}
      <SettingRow
        label="Refresh all data"
        description="Re-fetch all queries and system status"
        trailing={
          <button
            type="button"
            onClick={refreshAll}
            disabled={refreshing}
            className="p-2 rounded-[5px] text-text-tertiary hover:bg-hover hover:text-text-secondary transition-colors cursor-pointer disabled:opacity-40"
          >
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        }
      />
    </SettingSection>
  );
}

/* ── About Section ── */
function AboutSection() {
  return (
    <SettingSection icon={Info} title="About">
      <SettingRow
        label="Qareen"
        trailing={
          <span className="text-[12px] text-text-quaternary font-serif">by AOS</span>
        }
      />
      <SettingRow
        label="Interface"
        value="CENTCOM"
        description="Primary control surface"
      />
    </SettingSection>
  );
}

/* ── Main Page ── */
export default function SettingsPage() {
  return (
    <div className="bg-bg min-h-full overflow-y-auto">
      <div className="max-w-[640px] mx-auto px-5 md:px-8 py-6 md:py-10">
        <h1 className="text-[22px] font-[680] text-text tracking-[-0.025em] mb-1">Settings</h1>
        <p className="text-[13px] text-text-tertiary mb-8 font-serif">Configuration and system information</p>

        <ProfileSection />
        <PrayerSection />
        <AppearanceSection />
        <ConnectionsSection />
        <SystemSection />
        <AboutSection />
      </div>
    </div>
  );
}

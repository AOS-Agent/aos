import { useState, useCallback, useEffect, useRef } from 'react';
import { Moon as MoonIcon, Sun, Check, LocateFixed, Settings, ChevronDown } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useOperator, useUpdateOperator } from '@/hooks/useConfig';
import { SettingCard, SettingRow, LoadingRows } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// General — Profile + Appearance. Always-editable, auto-saves on blur.
// ---------------------------------------------------------------------------

/* ── Auto-saving field ── */

function Field({
  label,
  value,
  placeholder,
  onSave,
  className,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onSave: (value: string) => void;
  className?: string;
}) {
  const [local, setLocal] = useState(value);
  const [saved, setSaved] = useState(false);
  const original = useRef(value);

  useEffect(() => {
    setLocal(value);
    original.current = value;
  }, [value]);

  const handleBlur = useCallback(() => {
    const trimmed = local.trim();
    if (trimmed !== original.current) {
      onSave(trimmed);
      original.current = trimmed;
      setSaved(true);
      setTimeout(() => setSaved(false), 1200);
    }
  }, [local, onSave]);

  const inputId = label.toLowerCase().replace(/\s+/g, '-');

  return (
    <div className={className}>
      <div className="flex items-center justify-between mb-1.5">
        <label htmlFor={inputId} className="text-[11px] font-[510] text-text-quaternary">
          {label}
        </label>
        {saved && (
          <span className="flex items-center gap-1 text-[10px] text-green font-[510]">
            <Check className="w-2.5 h-2.5" /> Saved
          </span>
        )}
      </div>
      <input
        id={inputId}
        type="text"
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={handleBlur}
        placeholder={placeholder}
        className="
          w-full h-8 px-2.5 rounded-[5px]
          bg-bg-tertiary border border-border
          text-[13px] text-text-secondary
          placeholder:text-text-quaternary
          transition-colors duration-100
          hover:border-border-secondary
          focus:outline-none focus:border-accent/60
        "
      />
    </div>
  );
}

/* ── Auto-saving textarea ── */

function TextareaField({
  label,
  value,
  placeholder,
  onSave,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onSave: (value: string) => void;
}) {
  const [local, setLocal] = useState(value);
  const [saved, setSaved] = useState(false);
  const original = useRef(value);

  useEffect(() => {
    setLocal(value);
    original.current = value;
  }, [value]);

  const handleBlur = useCallback(() => {
    const trimmed = local.trim();
    if (trimmed !== original.current) {
      onSave(trimmed);
      original.current = trimmed;
      setSaved(true);
      setTimeout(() => setSaved(false), 1200);
    }
  }, [local, onSave]);

  const inputId = label.toLowerCase().replace(/\s+/g, '-');

  return (
    <div className="py-3 min-h-[44px]">
      <div className="flex items-center justify-between mb-1.5">
        <label htmlFor={inputId} className="text-[11px] font-[510] text-text-quaternary">
          {label}
        </label>
        {saved && (
          <span className="flex items-center gap-1 text-[10px] text-green font-[510]">
            <Check className="w-2.5 h-2.5" /> Saved
          </span>
        )}
      </div>
      <textarea
        id={inputId}
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={handleBlur}
        placeholder={placeholder}
        rows={3}
        className="
          w-full px-2.5 py-2 rounded-[5px]
          bg-bg-tertiary border border-border
          text-[13px] text-text-secondary
          placeholder:text-text-quaternary
          transition-colors duration-100 resize-none
          hover:border-border-secondary
          focus:outline-none focus:border-accent/60
        "
      />
    </div>
  );
}

/* ── Location field with auto-detect ── */

interface GeoResult {
  name: string;
  country: string;
  timezone: string;
  latitude: number;
  longitude: number;
}

function LocationField({
  value,
  onSave,
}: {
  value: string;
  onSave: (city: string, tz: string) => void;
}) {
  const [query, setQuery] = useState(value);
  const [results, setResults] = useState<GeoResult[]>([]);
  const [open, setOpen] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [saved, setSaved] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setQuery(value); }, [value]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Search cities via Open-Meteo geocoding (free, no key)
  const search = useCallback((q: string) => {
    clearTimeout(debounceRef.current);
    if (q.trim().length < 2) { setResults([]); setOpen(false); return; }
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await fetch(
          `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(q)}&count=10&language=en&format=json`,
        );
        if (!res.ok) return;
        const data = await res.json();
        // Deduplicate by name+country, keep first (highest population)
        const seen = new Set<string>();
        const items: GeoResult[] = [];
        for (const r of data.results ?? []) {
          const key = `${r.name}-${r.country}`;
          if (seen.has(key)) continue;
          seen.add(key);
          items.push({
            name: r.name,
            country: r.country ?? '',
            timezone: r.timezone ?? '',
            latitude: r.latitude,
            longitude: r.longitude,
          });
          if (items.length >= 5) break;
        }
        setResults(items);
        setOpen(items.length > 0);
      } catch { /* network error — silently ignore */ }
    }, 300);
  }, []);

  const selectResult = useCallback((r: GeoResult) => {
    setQuery(r.name);
    setOpen(false);
    setResults([]);
    onSave(r.name, r.timezone);
    setSaved(true);
    setTimeout(() => setSaved(false), 1200);
  }, [onSave]);

  const autoDetect = useCallback(async () => {
    if (!navigator.geolocation) return;
    setDetecting(true);
    try {
      const pos = await new Promise<GeolocationPosition>((resolve, reject) =>
        navigator.geolocation.getCurrentPosition(resolve, reject, { timeout: 8000 }),
      );
      const { latitude, longitude } = pos.coords;
      const res = await fetch(
        `https://geocoding-api.open-meteo.com/v1/search?name=${latitude.toFixed(1)}&count=1&format=json`,
      );
      // Fallback: use reverse lookup by searching nearby
      const reverseRes = await fetch(
        `https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&timezone=auto&forecast_days=1`,
      );
      let city = `${latitude.toFixed(2)}, ${longitude.toFixed(2)}`;
      let tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (reverseRes.ok) {
        const data = await reverseRes.json();
        if (data.timezone) tz = data.timezone;
      }
      // Try to get city name from geocoding search using coordinates
      const geoRes = await fetch(
        `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(tz.split('/').pop() ?? '')}&count=1&format=json`,
      );
      if (geoRes.ok) {
        const geoData = await geoRes.json();
        if (geoData.results?.[0]?.name) city = geoData.results[0].name;
      }
      setQuery(city);
      onSave(city, tz);
      setSaved(true);
      setTimeout(() => setSaved(false), 1200);
    } catch {
      // Geolocation denied — do nothing, user can type manually
    } finally {
      setDetecting(false);
    }
  }, [onSave]);

  return (
    <div ref={wrapperRef} className="py-3 relative">
      <div className="flex items-center justify-between mb-1.5">
        <label htmlFor="location" className="text-[11px] font-[510] text-text-quaternary">
          Location
        </label>
        {saved && (
          <span className="flex items-center gap-1 text-[10px] text-green font-[510]">
            <Check className="w-2.5 h-2.5" /> Saved
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <input
            id="location"
            type="text"
            value={query}
            onChange={(e) => { setQuery(e.target.value); search(e.target.value); }}
            placeholder="Type a city name"
            className="
              w-full h-8 px-2.5 rounded-[5px]
              bg-bg-tertiary border border-border
              text-[13px] text-text-secondary
              placeholder:text-text-quaternary
              transition-colors duration-100
              hover:border-border-secondary
              focus:outline-none focus:border-accent/60
            "
          />
          {open && results.length > 0 && (
            <div className="
              absolute top-full left-0 right-0 mt-1 z-50
              max-h-[200px] overflow-y-auto
              bg-bg-panel border border-border rounded-[7px]
              shadow-[0_4px_16px_rgba(0,0,0,0.3)]
            ">
              {results.map((r, i) => (
                <button
                  key={`${r.name}-${r.country}-${i}`}
                  onClick={() => selectResult(r)}
                  className="
                    w-full flex items-center justify-between px-3 py-2
                    text-left cursor-pointer
                    hover:bg-hover transition-colors duration-100
                  "
                >
                  <span className="text-[13px] text-text-secondary">{r.name}</span>
                  <span className="text-[11px] text-text-quaternary">{r.country}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={autoDetect}
          disabled={detecting}
          title="Auto-detect location"
          className="
            shrink-0 w-8 h-8 flex items-center justify-center
            rounded-[5px] bg-bg-tertiary border border-border
            text-text-tertiary hover:text-accent hover:border-border-secondary
            transition-colors duration-150 cursor-pointer
            disabled:opacity-40
          "
        >
          <LocateFixed className={`w-3.5 h-3.5 ${detecting ? 'animate-pulse' : ''}`} />
        </button>
      </div>
    </div>
  );
}

/* ── Profile card ── */

function ProfileCard() {
  const { data: op, isLoading } = useOperator();
  const updateOp = useUpdateOperator();

  const save = useCallback(
    (field: string, value: string) => {
      updateOp.mutate({ [field]: value || undefined } as any);
    },
    [updateOp],
  );

  const saveLocation = useCallback(
    (city: string, tz: string) => {
      updateOp.mutate({ timezone: tz } as any);
      // City is stored via the location object — for now save timezone which is what the system uses
    },
    [updateOp],
  );

  if (isLoading) {
    return (
      <SettingCard title="Profile">
        <LoadingRows count={3} />
      </SettingCard>
    );
  }

  const location = op?.location;
  const city = location?.city ?? location?.name ?? '';

  return (
    <SettingCard title="Profile">
      {/* Name + Nickname on same line */}
      <div className="grid grid-cols-2 gap-4 py-3">
        <Field
          label="Full name"
          value={op?.name ?? ''}
          onSave={(v) => save('name', v)}
        />
        <Field
          label="Qareen calls you"
          value={op?.nickname ?? ''}
          placeholder={op?.name?.split(' ')[0] ?? 'Nickname'}
          onSave={(v) => save('nickname', v)}
        />
      </div>

      <TextareaField
        label="Communication preferences"
        value={op?.prompt ?? ''}
        placeholder="e.g. Be concise and direct. Ask one question at a time. Use Arabic greetings."
        onSave={(v) => save('prompt', v)}
      />

      <LocationField value={city} onSave={saveLocation} />
    </SettingCard>
  );
}

/* ── Appearance card ── */

type ThemeMode = 'light' | 'auto' | 'dark';
type TextDensity = 'compact' | 'default' | 'large';

function applyTheme(mode: ThemeMode) {
  let resolved: 'dark' | 'light';
  if (mode === 'auto') {
    resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } else {
    resolved = mode;
  }
  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.classList.toggle('dark', resolved === 'dark');
}

function applyTextDensity(density: TextDensity) {
  const sizes: Record<TextDensity, string> = { compact: '13px', default: '', large: '16px' };
  document.documentElement.style.fontSize = sizes[density];
}

function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex items-center bg-bg-tertiary rounded-[7px] p-0.5 border border-border">
      {options.map((opt) => {
        const isActive = opt.value === value;
        return (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value)}
            className={`
              flex-1 px-2.5 py-1 rounded-[5px] text-[11px] font-[510] cursor-pointer
              transition-all duration-150
              ${isActive
                ? 'bg-[rgba(255,245,235,0.12)] text-text-secondary shadow-sm'
                : 'text-text-quaternary hover:text-text-tertiary'
              }
            `}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function AppearanceCard() {
  const [themeMode, setThemeMode] = useState<ThemeMode>('dark');
  const [textDensity, setTextDensity] = useState<TextDensity>('default');

  // Init from localStorage
  useEffect(() => {
    const savedTheme = localStorage.getItem('qareen-theme-mode') as ThemeMode | null;
    if (savedTheme) {
      setThemeMode(savedTheme);
      applyTheme(savedTheme);
    }
    const savedDensity = localStorage.getItem('qareen-text-density') as TextDensity | null;
    if (savedDensity) {
      setTextDensity(savedDensity);
      applyTextDensity(savedDensity);
    }
  }, []);

  // Listen for system theme changes when in auto mode
  useEffect(() => {
    if (themeMode !== 'auto') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => applyTheme('auto');
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [themeMode]);

  const handleTheme = useCallback((mode: ThemeMode) => {
    setThemeMode(mode);
    localStorage.setItem('qareen-theme-mode', mode);
    applyTheme(mode);
  }, []);

  const handleDensity = useCallback((density: TextDensity) => {
    setTextDensity(density);
    localStorage.setItem('qareen-text-density', density);
    applyTextDensity(density);
  }, []);

  return (
    <SettingCard title="Appearance">
      <div className="py-3">
        <span className="text-[11px] font-[510] text-text-quaternary block mb-2">Theme</span>
        <SegmentedControl
          options={[
            { value: 'light' as ThemeMode, label: 'Light' },
            { value: 'auto' as ThemeMode, label: 'Auto' },
            { value: 'dark' as ThemeMode, label: 'Dark' },
          ]}
          value={themeMode}
          onChange={handleTheme}
        />
      </div>
      <div className="py-3">
        <span className="text-[11px] font-[510] text-text-quaternary block mb-2">Text size</span>
        <SegmentedControl
          options={[
            { value: 'compact' as TextDensity, label: 'Compact' },
            { value: 'default' as TextDensity, label: 'Default' },
            { value: 'large' as TextDensity, label: 'Large' },
          ]}
          value={textDensity}
          onChange={handleDensity}
        />
      </div>
    </SettingCard>
  );
}

/* ── Time picker — custom dropdown, 30-min increments ── */

const TIME_OPTIONS: string[] = [];
for (let h = 0; h < 24; h++) {
  for (const m of ['00', '30']) {
    TIME_OPTIONS.push(`${String(h).padStart(2, '0')}:${m}`);
  }
}

function formatTime12(t: string): string {
  const [hStr, mStr] = t.split(':');
  const h = parseInt(hStr, 10);
  const suffix = h >= 12 ? 'PM' : 'AM';
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${h12}:${mStr} ${suffix}`;
}

function TimeField({
  label,
  value,
  onSave,
}: {
  label: string;
  value: string;
  onSave: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Scroll to current value when opened
  useEffect(() => {
    if (!open || !listRef.current) return;
    const idx = TIME_OPTIONS.indexOf(value);
    if (idx >= 0) {
      const el = listRef.current.children[idx] as HTMLElement | undefined;
      if (el) el.scrollIntoView({ block: 'center' });
    }
  }, [open, value]);

  const select = useCallback((t: string) => {
    setOpen(false);
    if (t !== value) {
      onSave(t);
      setSaved(true);
      setTimeout(() => setSaved(false), 1200);
    }
  }, [value, onSave]);

  return (
    <div ref={wrapperRef} className="relative">
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-[11px] font-[510] text-text-quaternary">{label}</label>
        {saved && (
          <span className="flex items-center gap-1 text-[10px] text-green font-[510]">
            <Check className="w-2.5 h-2.5" /> Saved
          </span>
        )}
      </div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="
          w-full h-8 px-2.5 rounded-[5px]
          bg-bg-tertiary border border-border
          flex items-center justify-between
          text-[13px] text-text-secondary
          transition-colors duration-100 cursor-pointer
          hover:border-border-secondary
        "
      >
        <span>{formatTime12(value)}</span>
        <ChevronDown className={`w-3 h-3 text-text-quaternary transition-transform duration-150 ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div
          ref={listRef}
          className="
            absolute top-full left-0 right-0 mt-1 z-50
            max-h-[200px] overflow-y-auto
            bg-bg-panel border border-border rounded-[7px]
            shadow-[0_4px_16px_rgba(0,0,0,0.3)]
          "
        >
          {TIME_OPTIONS.map((t) => (
            <button
              key={t}
              onClick={() => select(t)}
              className={`
                w-full px-3 py-1.5 text-left cursor-pointer
                text-[13px]
                transition-colors duration-100
                ${t === value
                  ? 'text-accent bg-[rgba(255,245,235,0.08)]'
                  : 'text-text-secondary hover:bg-hover'
                }
              `}
            >
              {formatTime12(t)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Schedule card ── */

function ScheduleCard() {
  const { data: op, isLoading } = useOperator();
  const updateOp = useUpdateOperator();

  const save = useCallback(
    (field: string, value: string) => {
      updateOp.mutate({ [field]: value || undefined } as any);
    },
    [updateOp],
  );

  if (isLoading) {
    return (
      <SettingCard title="Schedule">
        <LoadingRows count={2} />
      </SettingCard>
    );
  }

  return (
    <SettingCard title="Schedule">
      <div className="grid grid-cols-2 gap-4 py-3">
        <TimeField
          label="Morning briefing"
          value={op?.morning_briefing ?? '06:00'}
          onSave={(v) => save('morning_briefing', v)}
        />
        <TimeField
          label="Evening check-in"
          value={op?.evening_checkin ?? '21:00'}
          onSave={(v) => save('evening_checkin', v)}
        />
      </div>
      <div className="grid grid-cols-2 gap-4 py-3">
        <TimeField
          label="Quiet hours start"
          value={op?.quiet_hours_start ?? '23:00'}
          onSave={(v) => save('quiet_hours_start', v)}
        />
        <TimeField
          label="Quiet hours end"
          value={op?.quiet_hours_end ?? '06:00'}
          onSave={(v) => save('quiet_hours_end', v)}
        />
      </div>
    </SettingCard>
  );
}

/* ── General page ── */

function VersionFooter() {
  const { data: version } = useQuery({
    queryKey: ['version'],
    queryFn: async () => {
      const res = await fetch('/api/version');
      if (!res.ok) return null;
      const data = await res.json();
      return data.version as string;
    },
    staleTime: 600_000,
  });

  if (!version) return null;

  return (
    <div className="pt-4 pb-2 text-center">
      <span className="text-[11px] text-text-quaternary">
        AOS {version}
      </span>
    </div>
  );
}

function GeneralContent() {
  return (
    <>
      <ProfileCard />
      <ScheduleCard />
      <AppearanceCard />
      <VersionFooter />
    </>
  );
}

export const generalSection: SettingsSection = {
  id: 'general',
  title: 'General',
  icon: Settings,
  component: GeneralContent,
};

import { useState, useEffect } from 'react'
import {
  calcPrayerSchedule,
  getPrayerPeriodInfo,
  formatCountdown,
  DEFAULT_COORDS,
  type PrayerPeriodInfo,
} from '@/lib/prayer'

/**
 * ContextBar — ambient info bar at top center.
 * Time · Date · Prayer period + countdown · Weather.
 * Floating on the aurora, no container — just typography.
 */

// Weather from Open-Meteo (free, no key)
interface Weather {
  temp: number
  condition: string
  icon: string
}

const WMO: Record<number, { label: string; icon: string }> = {
  0: { label: 'Clear', icon: '☀' },
  1: { label: 'Clear', icon: '☀' },
  2: { label: 'Partly Cloudy', icon: '⛅' },
  3: { label: 'Overcast', icon: '☁' },
  45: { label: 'Fog', icon: '🌫' },
  51: { label: 'Drizzle', icon: '🌦' },
  53: { label: 'Drizzle', icon: '🌦' },
  61: { label: 'Rain', icon: '🌧' },
  63: { label: 'Rain', icon: '🌧' },
  65: { label: 'Heavy Rain', icon: '🌧' },
  71: { label: 'Snow', icon: '❄' },
  73: { label: 'Snow', icon: '❄' },
  75: { label: 'Heavy Snow', icon: '❄' },
  80: { label: 'Showers', icon: '🌦' },
  95: { label: 'Thunderstorm', icon: '⛈' },
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })
}

function formatDate(d: Date): string {
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
}

export function ContextBar() {
  const [now, setNow] = useState(new Date())
  const [prayer, setPrayer] = useState<PrayerPeriodInfo | null>(null)
  const [weather, setWeather] = useState<Weather | null>(null)

  // Live clock — every 30s
  useEffect(() => {
    const tick = () => {
      const n = new Date()
      setNow(n)
      try {
        const schedule = calcPrayerSchedule(DEFAULT_COORDS.latitude, DEFAULT_COORDS.longitude, n)
        setPrayer(getPrayerPeriodInfo(schedule, n))
      } catch {
        // adhan not loaded yet
      }
    }
    tick()
    const id = setInterval(tick, 30000)
    return () => clearInterval(id)
  }, [])

  // Weather — fetch once, cache 10min
  useEffect(() => {
    const { latitude, longitude } = DEFAULT_COORDS
    fetch(
      `https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&current=temperature_2m,weathercode&timezone=auto`,
    )
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.current) {
          const code = data.current.weathercode ?? 0
          const info = WMO[code] ?? { label: 'Unknown', icon: '☁' }
          setWeather({
            temp: Math.round(data.current.temperature_2m),
            condition: info.label,
            icon: info.icon,
          })
        }
      })
      .catch(() => {})
  }, [])

  return (
    <div className="fixed top-3 right-3 z-[200] pointer-events-none select-none">
      <div className="
        flex items-center gap-2.5 h-8 px-4
        rounded-full
        bg-bg-secondary/60 backdrop-blur-md
        border border-border/40
        shadow-[0_2px_12px_rgba(0,0,0,0.3)]
        text-[11px] tracking-wide
        font-[var(--font-sans)]
      " style={{ fontFamily: 'var(--font-sans)' }}>
        {/* Time */}
        <span className="text-text-secondary font-[510] tabular-nums">
          {formatTime(now)}
        </span>

        <Dot />

        {/* Date */}
        <span className="text-text-tertiary font-[450]">
          {formatDate(now)}
        </span>

        {/* Prayer */}
        {prayer && (
          <>
            <Dot />
            <span className="text-accent font-[510]">
              {prayer.label}
            </span>
            <Dot />
            <span className="text-text-tertiary font-[450]">
              {prayer.nextPrayer} in {formatCountdown(prayer.minutesUntilNext)}
            </span>
          </>
        )}

        {/* Weather */}
        {weather && (
          <>
            <Dot />
            <span className="text-text-tertiary font-[450]">
              {weather.temp}° {weather.condition}
            </span>
          </>
        )}
      </div>
    </div>
  )
}

function Dot() {
  return <span className="text-text-quaternary/40">·</span>
}

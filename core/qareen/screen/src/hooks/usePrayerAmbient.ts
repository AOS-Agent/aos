/**
 * usePrayerAmbient — applies a subtle prayer-period color tint to the app background.
 *
 * Calculates the current Islamic time period, returns a pair of CSS colors
 * for a radial gradient overlay. Updates every 60s. Theme-aware.
 *
 * The orb lives on the companion screen. This hook gives the REST of the app
 * a faint ambient warmth that shifts with the time of day.
 */

import { useState, useEffect, useCallback } from 'react'
import {
  calcPrayerSchedule,
  currentPrayerPeriod,
  DEFAULT_COORDS,
  type PrayerPeriod,
} from '@/lib/prayer'

type ColorPair = [string, string]

// Dark mode — muted tints on near-black
const DARK_COLORS: Record<PrayerPeriod, ColorPair> = {
  'last-third': ['#1A1535', '#0D0B20'],
  'fajr':       ['#2A1F3A', '#1A1228'],
  'sunrise':    ['#2A1A10', '#1A1208'],
  'duha':       ['#2A1C0A', '#1A1408'],
  'zawal':      ['#2A1E0A', '#1A1508'],
  'dhuhr':      ['#2A1E0A', '#1A1508'],
  'asr':        ['#2A1808', '#1A1206'],
  'pre-maghrib':['#2A1208', '#1A0E06'],
  'maghrib':    ['#2A0E0A', '#1A0A08'],
  'isha':       ['#1A1210', '#0D0B09'],
}

// Light mode — warm washes on paper
const LIGHT_COLORS: Record<PrayerPeriod, ColorPair> = {
  'last-third': ['#E8E0F0', '#F0EDF5'],
  'fajr':       ['#E8DDF0', '#F0E8F2'],
  'sunrise':    ['#F5E8D5', '#FAF0E0'],
  'duha':       ['#F5EAD0', '#FAF2E0'],
  'zawal':      ['#F5ECD0', '#FAF4E2'],
  'dhuhr':      ['#F5ECD0', '#FAF4E2'],
  'asr':        ['#F5E6D0', '#FAEDE0'],
  'pre-maghrib':['#F5DDD0', '#FAE5D8'],
  'maghrib':    ['#F5D8D0', '#FAE0D8'],
  'isha':       ['#F0EAE5', '#F5F0EC'],
}

function getTheme(): 'dark' | 'light' {
  return (document.documentElement.getAttribute('data-theme') as 'dark' | 'light') ?? 'dark'
}

export function usePrayerAmbient() {
  const [colors, setColors] = useState<ColorPair>(DARK_COLORS['isha'])
  const [period, setPeriod] = useState<PrayerPeriod>('isha')

  const update = useCallback(() => {
    const schedule = calcPrayerSchedule(DEFAULT_COORDS.latitude, DEFAULT_COORDS.longitude)
    const p = currentPrayerPeriod(schedule)
    const palette = getTheme() === 'light' ? LIGHT_COLORS : DARK_COLORS
    setPeriod(p)
    setColors(palette[p])
  }, [])

  useEffect(() => {
    update()
    const interval = setInterval(update, 60_000)

    // Re-evaluate on theme change
    const observer = new MutationObserver(update)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    })

    return () => {
      clearInterval(interval)
      observer.disconnect()
    }
  }, [update])

  return { colors, period }
}

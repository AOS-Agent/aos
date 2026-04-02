/**
 * Prayer time calculation using the adhan library.
 *
 * Calculates all 11 Islamic time periods precisely from coordinates.
 * Uses ISNA method (standard for North America).
 * Asr defaults to Shafi'i (majority opinion: shadow = object length).
 */

import { Coordinates, CalculationMethod, PrayerTimes, SunnahTimes, Madhab } from 'adhan'

export interface PrayerSchedule {
  fajr: Date
  sunrise: Date
  duhaStart: Date
  zawalStart: Date
  dhuhr: Date
  asr: Date
  preMaghribForbidden: Date
  maghrib: Date
  isha: Date
  islamicMidnight: Date
  lastThirdOfNight: Date
}

export type PrayerPeriod =
  | 'last-third'
  | 'fajr'
  | 'sunrise'
  | 'duha'
  | 'zawal'
  | 'dhuhr'
  | 'asr'
  | 'pre-maghrib'
  | 'maghrib'
  | 'isha'

export interface PrayerPeriodInfo {
  period: PrayerPeriod
  label: string
  icon: string
  nextPrayer: string
  nextPrayerTime: Date
  minutesUntilNext: number
  isForbidden: boolean
}

const PERIOD_META: Record<PrayerPeriod, { label: string; icon: string; forbidden: boolean }> = {
  'last-third':   { label: 'Last Third',  icon: '✦', forbidden: false },
  'fajr':         { label: 'Fajr',        icon: '☾', forbidden: false },
  'sunrise':      { label: 'Sunrise',     icon: '◐', forbidden: true },
  'duha':         { label: 'Duha',        icon: '☀', forbidden: false },
  'zawal':        { label: 'Zawal',       icon: '⏸', forbidden: true },
  'dhuhr':        { label: 'Dhuhr',       icon: '◑', forbidden: false },
  'asr':          { label: 'Asr',         icon: '◕', forbidden: false },
  'pre-maghrib':  { label: 'Pre-Maghrib', icon: '◔', forbidden: true },
  'maghrib':      { label: 'Maghrib',     icon: '●', forbidden: false },
  'isha':         { label: 'Isha',        icon: '☾', forbidden: false },
}

// Next obligatory prayer for each period
const NEXT_PRAYER_MAP: Record<PrayerPeriod, { name: string; key: keyof PrayerSchedule }> = {
  'last-third':   { name: 'Fajr',    key: 'fajr' },
  'fajr':         { name: 'Sunrise',  key: 'sunrise' },
  'sunrise':      { name: 'Duha',     key: 'duhaStart' },
  'duha':         { name: 'Dhuhr',    key: 'dhuhr' },
  'zawal':        { name: 'Dhuhr',    key: 'dhuhr' },
  'dhuhr':        { name: 'Asr',      key: 'asr' },
  'asr':          { name: 'Maghrib',  key: 'maghrib' },
  'pre-maghrib':  { name: 'Maghrib',  key: 'maghrib' },
  'maghrib':      { name: 'Isha',     key: 'isha' },
  'isha':         { name: 'Fajr',     key: 'fajr' }, // next day
}

export function calcPrayerSchedule(
  latitude: number,
  longitude: number,
  date: Date = new Date(),
): PrayerSchedule {
  const coords = new Coordinates(latitude, longitude)
  const params = CalculationMethod.NorthAmerica()
  params.madhab = Madhab.Shafi // majority opinion

  const pt = new PrayerTimes(coords, date, params)
  const st = new SunnahTimes(pt)

  return {
    fajr: pt.fajr,
    sunrise: pt.sunrise,
    duhaStart: new Date(pt.sunrise.getTime() + 15 * 60_000),
    zawalStart: new Date(pt.dhuhr.getTime() - 5 * 60_000),
    dhuhr: pt.dhuhr,
    asr: pt.asr,
    preMaghribForbidden: new Date((pt as any).sunset.getTime() - 15 * 60_000),
    maghrib: pt.maghrib,
    isha: pt.isha,
    islamicMidnight: st.middleOfTheNight,
    lastThirdOfNight: st.lastThirdOfTheNight,
  }
}

export function currentPrayerPeriod(schedule: PrayerSchedule, now: Date = new Date()): PrayerPeriod {
  const t = now.getTime()
  if (t < schedule.fajr.getTime()) return 'last-third'
  if (t < schedule.sunrise.getTime()) return 'fajr'
  if (t < schedule.duhaStart.getTime()) return 'sunrise'
  if (t < schedule.zawalStart.getTime()) return 'duha'
  if (t < schedule.dhuhr.getTime()) return 'zawal'
  if (t < schedule.asr.getTime()) return 'dhuhr'
  if (t < schedule.preMaghribForbidden.getTime()) return 'asr'
  if (t < schedule.maghrib.getTime()) return 'pre-maghrib'
  if (t < schedule.isha.getTime()) return 'maghrib'
  return 'isha'
}

export function getPrayerPeriodInfo(schedule: PrayerSchedule, now: Date = new Date()): PrayerPeriodInfo {
  const period = currentPrayerPeriod(schedule, now)
  const meta = PERIOD_META[period]
  const next = NEXT_PRAYER_MAP[period]
  const nextTime = schedule[next.key]

  let minutesUntilNext = Math.round((nextTime.getTime() - now.getTime()) / 60_000)
  // If next prayer is tomorrow's Fajr, we'd get a huge number — cap display
  if (minutesUntilNext < 0) minutesUntilNext = 0

  return {
    period,
    label: meta.label,
    icon: meta.icon,
    nextPrayer: next.name,
    nextPrayerTime: nextTime,
    minutesUntilNext,
    isForbidden: meta.forbidden,
  }
}

export function formatCountdown(minutes: number): string {
  if (minutes <= 0) return 'now'
  if (minutes < 60) return `${minutes}m`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

// Default coordinates: Mississauga, ON (Square One)
export const DEFAULT_COORDS = { latitude: 43.5933, longitude: -79.6441 }

import { useEffect, useRef, useState } from 'react'
import {
  calcPrayerSchedule,
  currentPrayerPeriod,
  DEFAULT_COORDS,
  type PrayerPeriod,
} from '@/lib/prayer'

/**
 * AuroraBackground — canvas-drawn flowing wave ribbons with CSS blur.
 * Wave colors shift based on the current Islamic prayer period.
 *
 * Fajr/Last Third → deep blue-violet (pre-dawn)
 * Duha/Sunrise → warm amber (morning)
 * Dhuhr → golden (midday)
 * Asr → mellow amber-orange (afternoon)
 * Maghrib → deep orange-red (sunset)
 * Isha → deep indigo-violet (night)
 */

// Color palettes per prayer period — each is [r, g, b] for the wave bands
type Palette = Array<{ r: number; g: number; b: number }>

const PALETTES: Record<PrayerPeriod, Palette> = {
  'last-third': [
    { r: 40, g: 30, b: 120 }, { r: 55, g: 35, b: 140 }, { r: 35, g: 25, b: 100 },
    { r: 70, g: 45, b: 150 }, { r: 45, g: 30, b: 110 }, { r: 30, g: 20, b: 90 }, { r: 50, g: 35, b: 130 },
  ],
  'fajr': [
    { r: 60, g: 45, b: 130 }, { r: 100, g: 60, b: 140 }, { r: 50, g: 35, b: 110 },
    { r: 80, g: 50, b: 120 }, { r: 120, g: 70, b: 100 }, { r: 40, g: 30, b: 100 }, { r: 90, g: 55, b: 130 },
  ],
  'sunrise': [
    { r: 200, g: 110, b: 40 }, { r: 180, g: 80, b: 30 }, { r: 160, g: 60, b: 20 },
    { r: 100, g: 50, b: 80 }, { r: 190, g: 90, b: 35 }, { r: 150, g: 55, b: 15 }, { r: 140, g: 50, b: 15 },
  ],
  'duha': [
    { r: 220, g: 125, b: 40 }, { r: 200, g: 90, b: 22 }, { r: 185, g: 68, b: 14 },
    { r: 70, g: 45, b: 100 }, { r: 210, g: 100, b: 25 }, { r: 165, g: 58, b: 12 }, { r: 150, g: 50, b: 10 },
  ],
  'zawal': [
    { r: 230, g: 160, b: 50 }, { r: 210, g: 130, b: 35 }, { r: 200, g: 110, b: 25 },
    { r: 80, g: 55, b: 90 }, { r: 220, g: 140, b: 40 }, { r: 190, g: 100, b: 20 }, { r: 170, g: 85, b: 15 },
  ],
  'dhuhr': [
    { r: 235, g: 175, b: 55 }, { r: 220, g: 145, b: 40 }, { r: 210, g: 120, b: 30 },
    { r: 90, g: 60, b: 85 }, { r: 225, g: 155, b: 45 }, { r: 200, g: 110, b: 25 }, { r: 180, g: 90, b: 18 },
  ],
  'asr': [
    { r: 215, g: 120, b: 35 }, { r: 195, g: 85, b: 20 }, { r: 180, g: 65, b: 12 },
    { r: 75, g: 48, b: 95 }, { r: 205, g: 100, b: 28 }, { r: 170, g: 60, b: 10 }, { r: 155, g: 50, b: 8 },
  ],
  'pre-maghrib': [
    { r: 200, g: 70, b: 20 }, { r: 180, g: 50, b: 15 }, { r: 160, g: 40, b: 10 },
    { r: 80, g: 40, b: 100 }, { r: 190, g: 60, b: 18 }, { r: 140, g: 35, b: 8 }, { r: 120, g: 30, b: 8 },
  ],
  'maghrib': [
    { r: 190, g: 55, b: 15 }, { r: 165, g: 40, b: 12 }, { r: 140, g: 30, b: 10 },
    { r: 90, g: 40, b: 110 }, { r: 175, g: 48, b: 14 }, { r: 120, g: 28, b: 8 }, { r: 100, g: 25, b: 8 },
  ],
  'isha': [
    { r: 50, g: 35, b: 120 }, { r: 65, g: 40, b: 140 }, { r: 40, g: 28, b: 100 },
    { r: 80, g: 50, b: 150 }, { r: 55, g: 35, b: 110 }, { r: 35, g: 22, b: 90 }, { r: 60, g: 38, b: 130 },
  ],
}

// Wave geometry (independent of color)
const BANDS = [
  { y: 0.08, thick: 0.18, freq: 0.0012, amp: 55, speed: 0.25, peak: 0.55 },
  { y: 0.22, thick: 0.20, freq: 0.0018, amp: 70, speed: 0.32, peak: 0.45 },
  { y: 0.38, thick: 0.22, freq: 0.0014, amp: 60, speed: 0.40, peak: 0.38 },
  { y: 0.52, thick: 0.16, freq: 0.0022, amp: 45, speed: 0.22, peak: 0.30 },
  { y: 0.65, thick: 0.18, freq: 0.0016, amp: 55, speed: 0.36, peak: 0.28 },
  { y: 0.78, thick: 0.14, freq: 0.0020, amp: 40, speed: 0.28, peak: 0.18 },
  { y: 0.90, thick: 0.12, freq: 0.0025, amp: 30, speed: 0.30, peak: 0.10 },
]

export function AuroraBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const tRef = useRef(0)
  const rafRef = useRef<number>(0)
  const [period, setPeriod] = useState<PrayerPeriod>('duha')

  // Update prayer period every 60s
  useEffect(() => {
    const update = () => {
      try {
        const schedule = calcPrayerSchedule(DEFAULT_COORDS.latitude, DEFAULT_COORDS.longitude, new Date())
        setPeriod(currentPrayerPeriod(schedule, new Date()))
      } catch {
        // adhan not loaded
      }
    }
    update()
    const id = setInterval(update, 60000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let W = 0
    let H = 0

    function resize() {
      W = window.innerWidth
      H = window.innerHeight
      canvas!.width = W
      canvas!.height = H
    }
    resize()
    window.addEventListener('resize', resize)

    const palette = PALETTES[period]

    function frame() {
      ctx!.clearRect(0, 0, W, H)
      const t = tRef.current

      for (let i = 0; i < BANDS.length; i++) {
        const b = BANDS[i]
        const c = palette[i] ?? palette[0]
        const centerY = H * b.y
        const halfH = H * b.thick * 0.5

        ctx!.beginPath()

        // Top edge
        for (let x = -30; x <= W + 30; x += 3) {
          const wave =
            Math.sin(x * b.freq + t * b.speed) * b.amp +
            Math.sin(x * b.freq * 0.7 + t * b.speed * 1.3 + i * 0.8) * b.amp * 0.5 +
            Math.cos(x * b.freq * 0.4 + t * b.speed * 0.7 + i * 2) * b.amp * 0.3
          const y = centerY - halfH + wave
          if (x <= -30) ctx!.moveTo(x, y)
          else ctx!.lineTo(x, y)
        }

        // Bottom edge
        for (let x = W + 30; x >= -30; x -= 3) {
          const wave =
            Math.sin(x * b.freq * 0.9 + t * b.speed * 0.85 + 1.5) * b.amp * 0.6 +
            Math.cos(x * b.freq * 0.5 + t * b.speed * 1.1 + i + 2) * b.amp * 0.35
          const y = centerY + halfH + wave
          ctx!.lineTo(x, y)
        }
        ctx!.closePath()

        const grad = ctx!.createLinearGradient(0, centerY - halfH * 1.5, 0, centerY + halfH * 1.5)
        grad.addColorStop(0, `rgba(${c.r},${c.g},${c.b}, 0)`)
        grad.addColorStop(0.25, `rgba(${c.r},${c.g},${c.b}, ${b.peak * 0.5})`)
        grad.addColorStop(0.5, `rgba(${c.r},${c.g},${c.b}, ${b.peak})`)
        grad.addColorStop(0.75, `rgba(${c.r},${c.g},${c.b}, ${b.peak * 0.5})`)
        grad.addColorStop(1, `rgba(${c.r},${c.g},${c.b}, 0)`)

        ctx!.fillStyle = grad
        ctx!.fill()
      }

      tRef.current += 0.012
      rafRef.current = requestAnimationFrame(frame)
    }

    rafRef.current = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(rafRef.current)
      window.removeEventListener('resize', resize)
    }
  }, [period])

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full"
      style={{ filter: 'blur(45px)', zIndex: 0 }}
      aria-hidden="true"
    />
  )
}

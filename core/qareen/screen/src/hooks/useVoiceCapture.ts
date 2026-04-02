/**
 * useVoiceCapture — connects browser microphone to the Qareen voice pipeline.
 *
 * Captures mic audio → streams via WebSocket to backend → VAD → STT → cards.
 * The VoiceIndicator reacts to voice_state SSE events automatically.
 */

import { useState, useCallback, useRef } from 'react'
import { useLiveMic } from './useLiveMic'

function getWsUrl(): string {
  const { hostname, protocol, port } = window.location
  const isSecure = protocol === 'https:'
  const wsProto = isSecure ? 'wss:' : 'ws:'

  // Dev server (Vite proxy handles /ws/audio → backend)
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    // Use Vite dev server port if available, otherwise direct to backend
    if (port === '5173') {
      return `ws://${hostname}:${port}/ws/audio`
    }
    return `ws://localhost:7700/ws/audio`
  }

  // Over network (Tailscale HTTPS) — use dedicated WSS proxy on port 7604
  if (isSecure) {
    return `wss://${hostname}:7604/ws/audio`
  }

  // HTTP over network — connect directly
  return `${wsProto}//${hostname}:7700/ws/audio`
}

async function checkMicAvailable(): Promise<{ available: boolean; error?: string }> {
  // Check if getUserMedia is supported
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return { available: false, error: 'Microphone not supported in this browser' }
  }

  // Check if any audio input devices exist
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    const audioInputs = devices.filter(d => d.kind === 'audioinput')
    if (audioInputs.length === 0) {
      return { available: false, error: 'No microphone found on this device' }
    }
  } catch {
    // enumerateDevices may fail without permission — that's ok, we'll try getUserMedia
  }

  return { available: true }
}

export function useVoiceCapture() {
  const [active, setActive] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const activeRef = useRef(false)
  const { start: startMic, stop: stopMic } = useLiveMic()

  const activate = useCallback(async (): Promise<boolean> => {
    if (activeRef.current) return true
    setError(null)

    // Pre-check mic availability
    const check = await checkMicAvailable()
    if (!check.available) {
      setError(check.error ?? 'Microphone unavailable')
      return false
    }

    try {
      const wsUrl = getWsUrl()
      console.log('[VoiceCapture] Connecting to:', wsUrl)
      const ok = await startMic(wsUrl)
      if (!ok) {
        setError('Failed to start microphone. Check browser permissions.')
        return false
      }
      activeRef.current = true
      setActive(true)
      return true
    } catch (e) {
      console.error('[VoiceCapture] Activation failed:', e)
      const msg = e instanceof Error ? e.message : 'Microphone activation failed'
      if (msg.includes('NotAllowed') || msg.includes('Permission')) {
        setError('Microphone permission denied. Allow mic access in browser settings.')
      } else if (msg.includes('NotFound')) {
        setError('No microphone found on this device.')
      } else if (msg.includes('WebSocket')) {
        setError('Voice server connection failed. Is the backend running?')
      } else {
        setError(msg)
      }
      return false
    }
  }, [startMic])

  const deactivate = useCallback(() => {
    stopMic()
    activeRef.current = false
    setActive(false)
    setError(null)
  }, [stopMic])

  const toggle = useCallback(async (): Promise<boolean> => {
    if (activeRef.current) {
      deactivate()
      return false
    }
    return activate()
  }, [activate, deactivate])

  return { active, error, activate, deactivate, toggle }
}

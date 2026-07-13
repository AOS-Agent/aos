/**
 * useQareenVoice — Plays TTS audio streamed from the Qareen backend.
 *
 * Connects to /ws/tts WebSocket. Sends text, receives MP3 chunks,
 * plays them sequentially via Web Audio API with minimal buffering.
 *
 * Supports barge-in: calling stop() cancels server-side generation
 * and clears the local audio queue immediately.
 */

import { useState, useCallback, useRef, useEffect } from 'react'

/** Resolve the TTS WebSocket URL based on current page context. */
function getTtsWsUrl(): string {
  const { hostname, protocol, port } = window.location
  // Dev server (Vite) -> talk to backend on port 4096
  if (port === '5173') return `ws://${hostname}:4096/ws/tts`
  // Production behind HTTPS reverse proxy
  if (protocol === 'https:') return `wss://${hostname}:7604/ws/tts`
  // Default: same host, same port
  return `ws://${hostname}:${port}/ws/tts`
}

export function useQareenVoice() {
  const [speaking, setSpeaking] = useState(false)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const chunksRef = useRef<ArrayBuffer[]>([])
  const playingRef = useRef(false)

  // ------------------------------------------------------------------
  // Audio chunk playback
  // ------------------------------------------------------------------

  const playChunks = useCallback(async () => {
    if (playingRef.current) return
    playingRef.current = true

    // Lazily create AudioContext (must happen after user gesture)
    if (!audioCtxRef.current) {
      const AC = window.AudioContext || (window as any).webkitAudioContext
      audioCtxRef.current = new AC()
    }

    const ctx = audioCtxRef.current
    if (ctx.state === 'suspended') await ctx.resume()

    while (chunksRef.current.length > 0) {
      const chunk = chunksRef.current.shift()!
      try {
        // decodeAudioData consumes the buffer, so pass a copy
        const audioBuffer = await ctx.decodeAudioData(chunk.slice(0))
        const source = ctx.createBufferSource()
        source.buffer = audioBuffer
        source.connect(ctx.destination)
        source.start()
        // Wait for this chunk to finish before playing the next
        await new Promise<void>((resolve) => {
          source.onended = () => resolve()
        })
      } catch {
        // Partial MP3 frames can't be decoded — skip silently.
        // This is expected at chunk boundaries.
      }
    }

    playingRef.current = false
  }, [])

  // ------------------------------------------------------------------
  // WebSocket connection
  // ------------------------------------------------------------------

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(getTtsWsUrl())
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => setConnected(true)

    ws.onclose = () => {
      setConnected(false)
      setSpeaking(false)
    }

    ws.onerror = () => {
      setConnected(false)
    }

    ws.onmessage = async (event) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary MP3 chunk — queue for sequential playback
        chunksRef.current.push(event.data)
        if (!playingRef.current) playChunks()
      } else {
        // JSON control message
        try {
          const msg = JSON.parse(event.data as string)
          if (msg.type === 'done' || msg.type === 'stopped') {
            setSpeaking(false)
          } else if (msg.type === 'error') {
            setSpeaking(false)
          }
        } catch {
          // Non-JSON text frame — ignore
        }
      }
    }
  }, [playChunks])

  // ------------------------------------------------------------------
  // Public API
  // ------------------------------------------------------------------

  /** Send text to the backend for TTS and start playback. */
  const speak = useCallback(
    (text: string) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        connect()
        // Retry once after connection establishes
        setTimeout(() => speak(text), 500)
        return
      }

      setSpeaking(true)
      chunksRef.current = []
      wsRef.current.send(JSON.stringify({ type: 'speak', text }))
    },
    [connect],
  )

  /** Stop current speech immediately (barge-in). */
  const stop = useCallback(() => {
    // Clear local audio queue
    chunksRef.current = []
    playingRef.current = false

    // Tell server to cancel generation
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'stop' }))
    }

    setSpeaking(false)
  }, [])

  // ------------------------------------------------------------------
  // Cleanup on unmount
  // ------------------------------------------------------------------

  useEffect(() => {
    return () => {
      wsRef.current?.close()
      audioCtxRef.current?.close()
    }
  }, [])

  return { speaking, connected, connect, speak, stop }
}

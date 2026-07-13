/**
 * useQuickVoice — Browser-native speech recognition for quick voice commands.
 *
 * Uses the SpeechRecognition API (Chrome, Safari, Edge) for instant
 * transcription of short commands. Separate from the full voice pipeline
 * used by Companion (which streams audio to backend for high-quality STT).
 */

import { useState, useCallback, useRef } from 'react'

type SpeechRecognitionType = typeof window extends { SpeechRecognition: infer T } ? T : any

function getSpeechRecognition(): SpeechRecognitionType | null {
  const w = window as any
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

export function useQuickVoice(onResult: (text: string) => void) {
  const [listening, setListening] = useState(false)
  const recognitionRef = useRef<any>(null)
  const supported = !!getSpeechRecognition()

  const start = useCallback(() => {
    const SR = getSpeechRecognition()
    if (!SR) return

    const recognition = new SR()
    recognitionRef.current = recognition
    recognition.continuous = false
    recognition.interimResults = false
    recognition.lang = 'en-US'
    recognition.maxAlternatives = 1

    recognition.onstart = () => setListening(true)

    recognition.onresult = (event: any) => {
      const transcript = event.results[0]?.[0]?.transcript
      if (transcript) onResult(transcript)
    }

    recognition.onerror = (event: any) => {
      // 'no-speech' and 'aborted' are normal — user didn't speak or cancelled
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        console.warn('[QuickVoice] Error:', event.error)
      }
    }

    recognition.onend = () => {
      setListening(false)
      recognitionRef.current = null
    }

    recognition.start()
  }, [onResult])

  const stop = useCallback(() => {
    recognitionRef.current?.stop()
  }, [])

  const toggle = useCallback(() => {
    if (listening) stop()
    else start()
  }, [listening, start, stop])

  return { listening, start, stop, toggle, supported }
}

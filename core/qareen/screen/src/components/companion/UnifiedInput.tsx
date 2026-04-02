import {
  useState,
  useCallback,
  useRef,
  useEffect,
  type FormEvent,
  type KeyboardEvent,
  type ChangeEvent,
} from 'react'
import { Mic, MicOff, Paperclip, Send } from 'lucide-react'
import { useVoiceCapture } from '@/hooks/useVoiceCapture'
import { useCompanionStore } from '@/store/companion'

// ---------------------------------------------------------------------------
// UnifiedInput — bottom input bar (spans full width).
//
// Text input with auto-resize, mic toggle, attachment button, send button.
// /command and @entity detection with dropdown suggestions.
// ---------------------------------------------------------------------------

interface UnifiedInputProps {
  onSendText: (text: string) => void
  onSendFile?: (file: File) => void
}

export function UnifiedInput({ onSendText, onSendFile }: UnifiedInputProps) {
  const [text, setText] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const { active: micActive, toggle: toggleMic, error: micError } = useVoiceCapture()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const addSegment = useCompanionStore((s) => s.addSegment)
  const addCard = useCompanionStore((s) => s.addCard)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [text])

  const handleSubmit = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault()
      const trimmed = text.trim()
      if (!trimmed || isSubmitting) return

      setIsSubmitting(true)
      setText('')

      // Optimistic transcript entry
      const segmentId = crypto.randomUUID()
      addSegment({
        id: segmentId,
        speaker: 'You',
        text: trimmed,
        timestamp: new Date().toISOString(),
        isProvisional: false,
      })

      try {
        const res = await fetch('/companion/input', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: trimmed, source: 'text' }),
        })
        if (res.ok) {
          const data = await res.json()
          if (data.card) addCard(data.card)
        }
      } catch {
        // Network error — optimistic segment stays
      } finally {
        setIsSubmitting(false)
        textareaRef.current?.focus()
      }

      onSendText(trimmed)
    },
    [text, isSubmitting, addSegment, addCard, onSendText],
  )

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter to send, Shift+Enter for newline
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSubmit()
      }
    },
    [handleSubmit],
  )

  const handleFileClick = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file && onSendFile) {
        onSendFile(file)
      }
      // Reset input
      if (e.target) e.target.value = ''
    },
    [onSendFile],
  )

  const session = useCompanionStore((s) => s.session)

  return (
    <div className="
      rounded-[14px]
      bg-[rgba(21,18,16,0.50)]
      backdrop-blur-[20px]
      border border-[rgba(255,245,235,0.08)]
      shadow-[0_4px_24px_rgba(0,0,0,0.3)]
      px-4 py-2.5 shrink-0
    ">
      {/* Mic error */}
      {micError && (
        <p className="text-[11px] text-red mb-2 px-1">{micError}</p>
      )}

      <div className="flex items-end gap-2.5">
        {/* Mic toggle — prominent when in a session */}
        <button
          type="button"
          onClick={() => toggleMic()}
          className={`
            shrink-0
            inline-flex items-center justify-center
            transition-all duration-[150ms] cursor-pointer
            ${session
              ? micActive
                ? 'h-10 w-10 rounded-full bg-red text-white animate-pulse'
                : 'h-10 w-10 rounded-full bg-accent/10 text-accent hover:bg-accent/20'
              : micActive
                ? 'h-8 w-8 rounded-full bg-red text-white animate-pulse'
                : 'h-8 w-8 rounded-full text-text-tertiary hover:text-text hover:bg-hover'
            }
          `}
          title={micActive ? 'Stop listening' : 'Start listening'}
        >
          {micActive ? <MicOff className={session ? 'w-5 h-5' : 'w-4 h-4'} /> : <Mic className={session ? 'w-5 h-5' : 'w-4 h-4'} />}
        </button>

        {/* Textarea */}
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={micActive ? 'Listening... or type here' : 'Type or speak... /command  @entity'}
            rows={1}
            disabled={isSubmitting}
            className="
              w-full min-h-[36px] max-h-[120px] px-3 py-2
              bg-[rgba(255,245,235,0.04)] border border-[rgba(255,245,235,0.06)] rounded-xl
              text-[13px] text-text placeholder:text-text-quaternary/60
              focus:border-[rgba(255,245,235,0.12)] focus:bg-[rgba(255,245,235,0.06)] focus:outline-none
              resize-none
              transition-all duration-[150ms]
            "
          />
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0 pb-0.5">
          {/* Attachment */}
          <button
            type="button"
            onClick={handleFileClick}
            className="
              h-8 w-8 rounded-full
              inline-flex items-center justify-center
              text-text-quaternary hover:text-text-secondary hover:bg-hover
              transition-all duration-[80ms]
              cursor-pointer
            "
            title="Attach file"
          >
            <Paperclip className="w-4 h-4" />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={handleFileChange}
          />

          {/* Send */}
          <button
            type="button"
            onClick={() => handleSubmit()}
            disabled={!text.trim() || isSubmitting}
            className="
              h-8 w-8 rounded-full
              inline-flex items-center justify-center
              bg-accent text-white
              disabled:opacity-20
              hover:bg-accent-hover
              transition-all duration-[80ms]
              cursor-pointer
            "
            title="Send"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}

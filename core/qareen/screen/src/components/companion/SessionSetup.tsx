import { useState, useCallback, useRef, useEffect, type KeyboardEvent } from 'react'
import { X, Plus, ArrowRight, ArrowLeft } from 'lucide-react'

// ---------------------------------------------------------------------------
// SessionSetup — centered glass card for configuring a new session.
//
// Floats on the aurora background. User types intent, adds people + context,
// then starts the session. Auto-detects session type from the intent text.
//
// Glass card: backdrop-blur, warm translucent bg-panel at 50%.
// Intent field: Garamond (serif). Chrome (buttons, pills, labels): Inter.
// ---------------------------------------------------------------------------

export interface SessionConfig {
  intent: string
  type: 'conversation' | 'processing'
  skill: string | null
  people: string[]
  context: string[]
}

interface SessionSetupProps {
  initialText?: string
  /** Pre-selected skill from pill click (thinking, meeting, planning, email) */
  initialSkill?: string | null
  onStart: (config: SessionConfig) => void
  onCancel: () => void
}

/** Detect session type + skill from the intent text. */
function detectType(text: string): { type: 'conversation' | 'processing'; skill: string | null } {
  const lower = text.toLowerCase()

  if (/\b(meeting|call|with\s+\w+)\b/.test(lower)) {
    return { type: 'processing', skill: 'meeting' }
  }
  if (/\b(plan|scope|break\s*down|decompose)\b/.test(lower)) {
    return { type: 'conversation', skill: 'planning' }
  }
  if (/\b(email|inbox|triage|gmail)\b/.test(lower)) {
    return { type: 'processing', skill: 'email' }
  }
  if (/\b(think|brainstorm|idea|ramble)\b/.test(lower)) {
    return { type: 'conversation', skill: 'thinking' }
  }

  return { type: 'conversation', skill: null }
}

export function SessionSetup({ initialText = '', initialSkill = null, onStart, onCancel }: SessionSetupProps) {
  const [intent, setIntent] = useState(initialText)
  const [people, setPeople] = useState<string[]>([])
  const [context, setContext] = useState<string[]>([])
  const [showPeopleInput, setShowPeopleInput] = useState(false)
  const [showContextInput, setShowContextInput] = useState(false)
  const [personQuery, setPersonQuery] = useState('')
  const [contextQuery, setContextQuery] = useState('')

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const personInputRef = useRef<HTMLInputElement>(null)
  const contextInputRef = useRef<HTMLInputElement>(null)

  // Focus the textarea on mount
  useEffect(() => {
    // Slight delay so the enter animation looks right
    const t = setTimeout(() => textareaRef.current?.focus(), 100)
    return () => clearTimeout(t)
  }, [])

  // Focus inline search inputs when toggled
  useEffect(() => {
    if (showPeopleInput) personInputRef.current?.focus()
  }, [showPeopleInput])
  useEffect(() => {
    if (showContextInput) contextInputRef.current?.focus()
  }, [showContextInput])

  // Auto-grow the textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.max(el.scrollHeight, 60)}px`
  }, [intent])

  // Add person
  const addPerson = useCallback((name: string) => {
    const trimmed = name.trim()
    if (!trimmed) return
    setPeople((prev) => (prev.includes(trimmed) ? prev : [...prev, trimmed]))
    setPersonQuery('')
  }, [])

  const removePerson = useCallback((name: string) => {
    setPeople((prev) => prev.filter((p) => p !== name))
  }, [])

  // Add context
  const addContext = useCallback((item: string) => {
    const trimmed = item.trim()
    if (!trimmed) return
    setContext((prev) => (prev.includes(trimmed) ? prev : [...prev, trimmed]))
    setContextQuery('')
  }, [])

  const removeContext = useCallback((item: string) => {
    setContext((prev) => prev.filter((c) => c !== item))
  }, [])

  // Enter in person/context input adds the item
  const handlePersonKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        addPerson(personQuery)
      }
      if (e.key === 'Escape') {
        setShowPeopleInput(false)
        setPersonQuery('')
      }
    },
    [personQuery, addPerson],
  )

  const handleContextKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        addContext(contextQuery)
      }
      if (e.key === 'Escape') {
        setShowContextInput(false)
        setContextQuery('')
      }
    },
    [contextQuery, addContext],
  )

  // Start session — prefer initialSkill from pill click, fallback to text detection
  const handleStart = useCallback(() => {
    const trimmed = intent.trim()
    if (!trimmed) return

    const detected = detectType(trimmed)
    const skill = initialSkill ?? detected.skill
    const type = initialSkill
      ? (['meeting', 'email'].includes(initialSkill) ? 'processing' as const : 'conversation' as const)
      : detected.type
    onStart({
      intent: trimmed,
      type,
      skill,
      people,
      context,
    })
  }, [intent, people, context, initialSkill, onStart])

  // Cmd+Enter to start
  const handleTextareaKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        handleStart()
      }
      if (e.key === 'Escape') {
        onCancel()
      }
    },
    [handleStart, onCancel],
  )

  const detectedSkill = initialSkill ?? detectType(intent).skill
  const SKILL_LABELS: Record<string, string> = {
    meeting: 'Meeting', planning: 'Planning', email: 'Declutter', thinking: 'Thinking',
  }
  const skillLabel = detectedSkill ? (SKILL_LABELS[detectedSkill] ?? detectedSkill) : null

  return (
    <div className="flex-1 flex items-center justify-center px-4 sm:px-6 session-setup-enter">
      {/* Glass card */}
      <div
        className="
          w-full max-w-[520px]
          rounded-[14px]
          backdrop-blur-[20px]
          bg-[rgba(21,18,16,0.50)]
          border border-[rgba(255,245,235,0.08)]
          shadow-[0_4px_24px_rgba(0,0,0,0.3)]
          p-6 sm:p-8
        "
      >
        {/* Header: back button + skill badge */}
        <div className="flex items-center justify-between mb-4">
          <button
            onClick={onCancel}
            className="
              inline-flex items-center gap-1.5
              text-[12px] text-text-quaternary hover:text-text-secondary
              transition-colors cursor-pointer
            "
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back
          </button>
          {skillLabel && (
            <span className="text-[10px] font-[510] text-accent tracking-wide uppercase">
              {skillLabel}
            </span>
          )}
        </div>

        {/* Intent textarea — Garamond, large */}
        <textarea
          ref={textareaRef}
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          onKeyDown={handleTextareaKeyDown}
          placeholder="What are we working on?"
          rows={2}
          className="
            w-full min-h-[60px] max-h-[200px] px-0 py-2
            bg-transparent border-none
            text-[16px] text-text-secondary placeholder:text-text-quaternary/70
            focus:outline-none
            resize-none leading-relaxed
          "
          style={{ fontFamily: 'var(--font-serif)' }}
        />

        {/* Separator */}
        <div className="border-t border-[rgba(255,245,235,0.06)] my-4" />

        {/* People section */}
        <div className="mb-4">
          {/* People pills */}
          {people.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {people.map((name) => (
                <span
                  key={name}
                  className="
                    inline-flex items-center gap-1 h-7 px-3
                    bg-[rgba(255,245,235,0.06)] border border-[rgba(255,245,235,0.08)]
                    rounded-full text-[12px] text-text-secondary font-[450]
                  "
                >
                  {name}
                  <button
                    onClick={() => removePerson(name)}
                    className="ml-0.5 text-text-quaternary hover:text-text transition-colors cursor-pointer"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Add people button / inline input */}
          {showPeopleInput ? (
            <input
              ref={personInputRef}
              type="text"
              value={personQuery}
              onChange={(e) => setPersonQuery(e.target.value)}
              onKeyDown={handlePersonKeyDown}
              onBlur={() => {
                if (personQuery.trim()) addPerson(personQuery)
                setShowPeopleInput(false)
              }}
              placeholder="Name..."
              className="
                w-48 h-7 px-2
                bg-[rgba(255,245,235,0.04)] border border-[rgba(255,245,235,0.10)]
                rounded-md text-[12px] text-text placeholder:text-text-quaternary
                focus:outline-none focus:border-accent/30
              "
            />
          ) : (
            <button
              onClick={() => setShowPeopleInput(true)}
              className="
                inline-flex items-center gap-1.5
                text-[12px] text-text-quaternary hover:text-text-secondary
                transition-colors cursor-pointer
              "
            >
              <Plus className="w-3 h-3" />
              Add people
            </button>
          )}
        </div>

        {/* Context section */}
        <div className="mb-6">
          {/* Context pills */}
          {context.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {context.map((item) => (
                <span
                  key={item}
                  className="
                    inline-flex items-center gap-1 h-7 px-3
                    bg-[rgba(255,245,235,0.06)] border border-[rgba(255,245,235,0.08)]
                    rounded-full text-[12px] text-text-secondary font-[450]
                  "
                >
                  {item}
                  <button
                    onClick={() => removeContext(item)}
                    className="ml-0.5 text-text-quaternary hover:text-text transition-colors cursor-pointer"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Add context button / inline input */}
          {showContextInput ? (
            <input
              ref={contextInputRef}
              type="text"
              value={contextQuery}
              onChange={(e) => setContextQuery(e.target.value)}
              onKeyDown={handleContextKeyDown}
              onBlur={() => {
                if (contextQuery.trim()) addContext(contextQuery)
                setShowContextInput(false)
              }}
              placeholder="Project, file, or vault note..."
              className="
                w-64 h-7 px-2
                bg-[rgba(255,245,235,0.04)] border border-[rgba(255,245,235,0.10)]
                rounded-md text-[12px] text-text placeholder:text-text-quaternary
                focus:outline-none focus:border-accent/30
              "
            />
          ) : (
            <button
              onClick={() => setShowContextInput(true)}
              className="
                inline-flex items-center gap-1.5
                text-[12px] text-text-quaternary hover:text-text-secondary
                transition-colors cursor-pointer
              "
            >
              <Plus className="w-3 h-3" />
              Add context
            </button>
          )}
        </div>

        {/* Start button — right-aligned, accent orange */}
        <div className="flex items-center justify-between">
          <button
            onClick={onCancel}
            className="
              text-[12px] text-text-quaternary hover:text-text-secondary
              transition-colors cursor-pointer
            "
          >
            Cancel
          </button>

          <button
            onClick={handleStart}
            disabled={!intent.trim()}
            className="
              inline-flex items-center gap-2
              h-10 px-6 rounded-full
              bg-accent text-white text-[13px] font-[510]
              hover:bg-accent-hover
              disabled:opacity-30
              transition-all duration-[150ms]
              cursor-pointer
            "
          >
            Start session
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}

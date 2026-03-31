import { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, Square, Pause, Play, ArrowLeft, Clock, ChevronRight, Loader2, Trash2, Calendar, Users } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAudioAnalyser } from '@/hooks/useAudioAnalyser';
import { useLiveMic } from '@/hooks/useLiveMic';
import { EmptyState } from '@/components/primitives';

const COMPANION = '/companion';
const COMPANION_DIRECT = typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname}:${window.location.protocol === 'https:' ? '7604' : '7603'}` : 'http://localhost:7603';
const WS_COMPANION = typeof window !== 'undefined' ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:${window.location.protocol === 'https:' ? '7604' : '7603'}` : 'ws://localhost:7603';

type Phase = 'idle' | 'recording' | 'paused' | 'generating' | 'detail';
interface TranscriptBlock { speaker: string; text: string; timestamp: number; start_time: string; partial?: boolean; finalized?: string; draft?: string; }
interface MeetingItem { id: string; title: string; date: string; duration_seconds: number; has_transcript: boolean; has_summary: boolean; summary?: string; transcript?: TranscriptBlock[]; notes?: Record<string, string[]>; participants?: string[]; audio_path?: string; }

export default function MeetingPage() {
  const [phase, setPhase] = useState<Phase>('idle');
  const [transcript, setTranscript] = useState<TranscriptBlock[]>([]);
  const [notes, setNotes] = useState<Record<string, string[]>>({});
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [summary, setSummary] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [online, setOnline] = useState(false);
  const [meetings, setMeetings] = useState<MeetingItem[]>([]);
  const [selectedMeeting, setSelectedMeeting] = useState<MeetingItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [participantInput, setParticipantInput] = useState('');
  const [detailTab, setDetailTab] = useState<'summary' | 'transcript'>('summary');
  const [manualInput, setManualInput] = useState('');
  const transcriptRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sseRef = useRef<EventSource | null>(null);
  const { start: startAudio, stop: stopAudio, getAmplitude } = useAudioAnalyser();
  const { start: startLiveMic, stop: stopLiveMic } = useLiveMic();

  useEffect(() => { let m = true; async function check() { try { const r = await fetch(`${COMPANION}/health`, { signal: AbortSignal.timeout(2000) }); if (m) setOnline(r.ok); } catch { if (m) setOnline(false); } } check(); const iv = setInterval(check, 5000); return () => { m = false; clearInterval(iv); }; }, []);

  const loadMeetings = useCallback(async () => { try { const r = await fetch(`${COMPANION}/meetings`); if (r.ok) setMeetings(await r.json()); } catch { /* empty */ } }, []);
  useEffect(() => { loadMeetings(); }, [loadMeetings]);

  useEffect(() => { if (autoScrollRef.current && transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight; }, [transcript]);
  useEffect(() => { if (phase === 'recording') { timerRef.current = setInterval(() => setElapsed(e => e + 1), 1000); } else if (phase === 'paused' && timerRef.current) clearInterval(timerRef.current); return () => { if (timerRef.current) clearInterval(timerRef.current); }; }, [phase]);
  const formatTime = (s: number) => { const mm = String(Math.floor(s / 60)).padStart(2, '0'); const ss = String(s % 60).padStart(2, '0'); return `${mm}:${ss}`; };

  const connectSSE = useCallback(() => {
    const sse = new EventSource(`${COMPANION_DIRECT}/stream`); sseRef.current = sse;
    sse.addEventListener('transcript', e => { const d = JSON.parse(e.data); setTranscript(p => [...p.filter((b: any) => !b.partial), d]); });
    sse.addEventListener('transcript_partial', e => { const d = JSON.parse(e.data); setTranscript(p => [...p.filter((b: any) => !b.partial), { ...d, speaker: 'You', partial: true, finalized: d.finalized || '', draft: d.draft || '' }]); });
    sse.addEventListener('meeting_notes', e => { const d = JSON.parse(e.data); setNotes(p => ({ ...p, [d.topic]: [...(p[d.topic] || []), ...d.notes] })); });
    sse.addEventListener('meeting_suggestion', e => { const d = JSON.parse(e.data); setSuggestion(d.text); setTimeout(() => setSuggestion(null), 20000); });
    sse.addEventListener('meeting_state', e => { const d = JSON.parse(e.data); if (d.state === 'summary' && d.summary) setSummary(d.summary); });
    sse.onerror = () => { sse.close(); setTimeout(() => { if (phase === 'recording' || phase === 'paused') connectSSE(); }, 2000); };
  }, [phase]);

  const handleRecord = async () => {
    setError(null); setTranscript([]); setNotes({}); setSuggestion(null); setSummary(''); setElapsed(0); setPhase('recording');
    try {
      await startLiveMic(`${WS_COMPANION}/ws/mic`); await startAudio();
      await fetch(`${COMPANION}/meeting/create`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: '', participants: participantInput.trim() ? participantInput.split(',').map(p => p.trim()).filter(Boolean) : [] }) });
      connectSSE();
      await fetch(`${COMPANION}/meeting/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source: 'remote' }) });
    } catch { setPhase('idle'); stopLiveMic(); setError('Could not start recording.'); }
  };

  const handleStop = async () => {
    if (timerRef.current) clearInterval(timerRef.current); stopAudio(); stopLiveMic(); sseRef.current?.close();
    setSelectedMeeting({ id: '', title: '', date: new Date().toISOString(), duration_seconds: elapsed, has_transcript: transcript.length > 0, has_summary: false, transcript: [...transcript], notes: { ...notes } } as MeetingItem);
    setPhase('detail'); setDetailTab('summary');
    try { const r = await fetch(`${COMPANION}/meeting/end`, { method: 'POST' }); const d = await r.json(); setSummary(d.summary || ''); if (d.meeting_id) { try { const dr = await fetch(`${COMPANION}/meetings/${d.meeting_id}`); if (dr.ok) { const fm = await dr.json(); setSelectedMeeting(fm); setSummary(fm.summary || d.summary || ''); } } catch { /* empty */ } } } catch { setSummary('Summary generation failed.'); }
    await loadMeetings();
  };

  const openMeeting = async (meeting: MeetingItem) => { try { const r = await fetch(`${COMPANION}/meetings/${meeting.id}`); if (r.ok) { const d = await r.json(); setSelectedMeeting(d); setSummary(d.summary || ''); setTranscript(d.transcript || []); setNotes(d.notes || {}); setPhase('detail'); } } catch { /* empty */ } };

  const handleDelete = async (e: React.MouseEvent, id: string) => { e.stopPropagation(); if (deleteConfirm === id) { try { await fetch(`${COMPANION}/meetings/${id}`, { method: 'DELETE' }); await loadMeetings(); } catch { /* empty */ } setDeleteConfirm(null); } else { setDeleteConfirm(id); setTimeout(() => setDeleteConfirm(null), 3000); } };

  // DETAIL VIEW
  if (phase === 'detail' && selectedMeeting) {
    const meetDate = selectedMeeting.date ? new Date(selectedMeeting.date) : null;
    return (
      <div className="flex flex-col h-full">
        <div className="shrink-0 px-5 md:px-8 pt-4 pb-3 border-b border-border">
          <button onClick={() => { setPhase('idle'); setSelectedMeeting(null); }} className="flex items-center gap-1.5 text-[12px] text-text-quaternary hover:text-text-secondary mb-4 min-h-[44px] md:min-h-0"><ArrowLeft className="w-3.5 h-3.5" />Meetings</button>
          <h2 className="text-[22px] font-[680] text-text tracking-[-0.025em]">{selectedMeeting.title || 'Untitled Meeting'}</h2>
          <div className="flex items-center gap-2 mt-2.5 flex-wrap">
            {meetDate && <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-[4px] bg-bg-secondary text-[11px] text-text-tertiary"><Calendar className="w-3 h-3" />{meetDate.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}</span>}
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-[4px] bg-bg-secondary text-[11px] text-text-tertiary"><Clock className="w-3 h-3" />{formatTime(selectedMeeting.duration_seconds || 0)}</span>
            {selectedMeeting.participants && selectedMeeting.participants.length > 0 && <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-[4px] bg-bg-secondary text-[11px] text-text-tertiary"><Users className="w-3 h-3" />{selectedMeeting.participants.join(', ')}</span>}
          </div>
          <div className="flex gap-px mt-4 bg-bg-secondary rounded-[6px] p-0.5 w-fit">
            <button onClick={() => setDetailTab('summary')} className={`px-4 py-1.5 rounded-[5px] text-[12px] font-[510] transition-all ${detailTab === 'summary' ? 'bg-bg-tertiary text-text shadow-sm' : 'text-text-quaternary hover:text-text-tertiary'}`}>Summary</button>
            <button onClick={() => setDetailTab('transcript')} className={`px-4 py-1.5 rounded-[5px] text-[12px] font-[510] transition-all ${detailTab === 'transcript' ? 'bg-bg-tertiary text-text shadow-sm' : 'text-text-quaternary hover:text-text-tertiary'}`}>Transcript ({(selectedMeeting.transcript || []).length})</button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-[680px] mx-auto px-5 md:px-8 py-6">
            {detailTab === 'transcript' ? (
              <div className="space-y-3">{(selectedMeeting.transcript || []).length === 0 ? <p className="text-[13px] text-text-quaternary py-8 text-center">No transcript available.</p> : (selectedMeeting.transcript || []).map((block, i) => <div key={i}><div className="flex items-baseline gap-2 mb-0.5"><span className="text-[11px] font-[590] text-accent">{block.speaker}</span><span className="text-[10px] font-mono text-text-quaternary">{block.start_time}</span></div><p className="text-[14px] text-text-secondary leading-[1.65]">{block.text}</p></div>)}</div>
            ) : (
              <div className="space-y-5">
                {summary || selectedMeeting.summary ? <div className="meeting-prose"><ReactMarkdown remarkPlugins={[remarkGfm]}>{summary || selectedMeeting.summary || ''}</ReactMarkdown></div> : <div className="flex flex-col items-center py-12"><Loader2 className="w-5 h-5 text-accent animate-spin mb-3" /><p className="text-[13px] text-text-tertiary">Generating summary...</p></div>}
                {selectedMeeting.notes?.Tasks && selectedMeeting.notes.Tasks.length > 0 && <div className="p-5 rounded-[8px] bg-bg-secondary border border-border-secondary"><div className="type-overline text-text-tertiary mb-3">Action Items</div><div className="space-y-2.5">{selectedMeeting.notes.Tasks.map((task, i) => <label key={i} className="flex items-start gap-3 cursor-pointer"><input type="checkbox" className="mt-1 accent-accent w-4 h-4" /><span className="text-[13px] text-text leading-relaxed">{task}</span></label>)}</div></div>}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // RECORDING STATE
  if (phase === 'recording' || phase === 'paused') {
    return (
      <div className="flex flex-col h-full">
        <div className="shrink-0 flex items-center justify-between px-5 md:px-6 h-12 border-b border-border bg-bg-panel">
          <div className="flex items-center gap-3">{phase === 'recording' && <div className="w-2 h-2 rounded-full bg-red animate-pulse" />}{phase === 'paused' && <div className="w-2 h-2 rounded-full bg-text-quaternary" />}<span className="text-[13px] font-mono text-text-secondary">{formatTime(elapsed)}</span></div>
          <div className="flex items-center gap-2">
            <button onClick={async () => { if (phase === 'recording') { await fetch(`${COMPANION}/meeting/pause`, { method: 'POST' }); setPhase('paused'); } else { await fetch(`${COMPANION}/meeting/resume`, { method: 'POST' }); setPhase('recording'); } }} className="w-8 h-8 flex items-center justify-center rounded-[5px] hover:bg-hover text-text-tertiary">{phase === 'recording' ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}</button>
            <button onClick={handleStop} className="px-3 py-1.5 rounded-[5px] bg-red/10 text-red text-[12px] font-[590] hover:bg-red/20">Done</button>
          </div>
        </div>
        <div className="flex-1 flex flex-col md:flex-row min-h-0">
          <div className="flex-1 md:w-[360px] md:shrink-0 md:flex-none flex flex-col md:border-r border-border">
            <div ref={transcriptRef} className="flex-1 overflow-y-auto px-5 md:px-6 py-4">
              {transcript.length === 0 ? <p className="text-[13px] text-text-quaternary text-center py-8">{phase === 'recording' ? 'Listening...' : 'Paused.'}</p> : transcript.map((block: any, i) => <div key={i} className="mb-3"><div className="flex items-baseline gap-2"><span className="text-[11px] font-[590] text-accent">{block.speaker}</span><span className="text-[10px] font-mono text-text-quaternary">{block.start_time}</span>{block.partial && <span className="text-[9px] text-accent/60 animate-pulse ml-1">live</span>}</div>{block.partial ? <p className="text-[14px] leading-relaxed"><span className="text-text">{block.finalized}</span>{block.draft && <span className="text-text-tertiary"> {block.draft}</span>}</p> : <p className="text-[14px] leading-relaxed text-text">{block.text}</p>}</div>)}
            </div>
          </div>
          <div className="shrink-0 md:shrink md:flex-1 flex flex-col min-h-0 border-t md:border-t-0 border-border max-h-[40vh] md:max-h-none">
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
              {suggestion && <div className="p-3 rounded-[5px] bg-accent-subtle border-l-[3px] border-accent"><div className="type-overline text-accent mb-1">Ask Next</div><p className="text-[13px] font-[510] text-text leading-snug">{suggestion}</p></div>}
              {notes['Tasks'] && notes['Tasks'].length > 0 && <div><div className="type-overline text-text-quaternary mb-2">Action Items</div><div className="space-y-1">{notes['Tasks'].map((t, i) => <div key={i} className="flex items-start gap-2.5 py-1"><input type="checkbox" className="mt-1 accent-accent w-3.5 h-3.5" /><p className="text-[12px] text-text-secondary leading-relaxed flex-1">{t}</p></div>)}</div></div>}
              {!suggestion && Object.keys(notes).length === 0 && <div className="text-center py-12"><p className="text-[12px] text-text-quaternary">Intelligence will appear as you speak.</p></div>}
            </div>
            <div className="shrink-0 px-5 py-3 border-t border-border">
              <div className="flex items-center gap-2"><input type="text" value={manualInput} onChange={e => setManualInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && manualInput.trim()) { setNotes(p => ({ ...p, 'Manual Notes': [...(p['Manual Notes'] || []), manualInput.trim()] })); setManualInput(''); } }} placeholder="Type a note..." className="flex-1 bg-bg-tertiary rounded-[5px] px-3 py-2 text-[13px] text-text placeholder:text-text-quaternary border border-border focus:border-border-secondary focus:outline-none" /></div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // IDLE — Meeting list
  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 px-5 md:px-6 pt-5 pb-3">
        <div className="flex items-center justify-between"><h1 className="type-title">Meetings</h1><div className="flex items-center gap-2"><div className={`w-1.5 h-1.5 rounded-full ${online ? 'bg-green' : 'bg-red'}`} /><span className={`text-[10px] font-[510] ${online ? 'text-green' : 'text-red'}`}>{online ? 'Ready' : 'Offline'}</span></div></div>
      </div>
      <div className="flex-1 overflow-y-auto px-5 md:px-6">
        {error && <div className="mb-4 p-3 rounded-[5px] bg-red-muted border border-red/20"><p className="text-[12px] text-red">{error}</p></div>}
        {meetings.length === 0 ? (
          <EmptyState icon={<Mic />} title="No meetings yet" description="Tap the button below to start recording." />
        ) : (
          <div className="space-y-px">{meetings.map(m => (
            <button key={m.id} onClick={() => openMeeting(m)} className="w-full text-left px-4 py-3 rounded-[5px] hover:bg-hover transition-colors group" style={{ transitionDuration: 'var(--duration-instant)' }}>
              <div className="flex items-start justify-between"><div className="min-w-0 flex-1"><p className="text-[13px] font-[590] text-text truncate">{m.title || 'Untitled Meeting'}</p><p className="text-[11px] text-text-quaternary mt-0.5">{m.date ? new Date(m.date).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''}</p></div><div className="flex items-center gap-2 shrink-0 ml-3"><span className="text-[11px] font-mono text-text-quaternary">{formatTime(m.duration_seconds || 0)}</span><button onClick={e => handleDelete(e, m.id)} className={`rounded-[4px] transition-all ${deleteConfirm === m.id ? 'px-2 bg-red text-white opacity-100' : 'w-6 h-6 flex items-center justify-center text-text-quaternary hover:text-red opacity-0 group-hover:opacity-100'}`}>{deleteConfirm === m.id ? <span className="text-[10px] font-[590]">Delete?</span> : <Trash2 className="w-3 h-3" />}</button><ChevronRight className="w-3.5 h-3.5 text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity" /></div></div>
            </button>
          ))}</div>
        )}
      </div>
      <div className="shrink-0 flex flex-col items-center gap-3 py-5 border-t border-border bg-bg-panel">
        <input type="text" value={participantInput} onChange={e => setParticipantInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && online) handleRecord(); }} placeholder="Meeting with... (optional)" className="w-[260px] text-center bg-transparent border-none outline-none text-[13px] text-text-quaternary placeholder:text-text-quaternary/50 focus:text-text-tertiary" />
        <button onClick={handleRecord} disabled={!online} className={`w-16 h-16 rounded-full flex items-center justify-center transition-all ${online ? 'bg-red hover:bg-red/90 hover:scale-105 active:scale-95 shadow-[0_0_20px_rgba(255,69,58,0.3)]' : 'bg-bg-tertiary cursor-not-allowed'}`} style={{ transitionDuration: '150ms' }}><Mic className={`w-6 h-6 ${online ? 'text-white' : 'text-text-quaternary'}`} /></button>
      </div>
    </div>
  );
}

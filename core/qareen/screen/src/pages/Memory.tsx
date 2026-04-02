import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Calendar, Search, X, ChevronRight, BookOpen } from 'lucide-react';

const API = '/api';

interface LogEntry { date: string; title: string; path: string }
interface VaultFile { path: string; title?: string; content: string; frontmatter: Record<string, unknown>; size_bytes: number }

export default function Memory() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [file, setFile] = useState<VaultFile | null>(null);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const contentRef = useRef<HTMLDivElement>(null);

  // Fetch log entries on mount
  useEffect(() => {
    setLoading(true);
    fetch(`${API}/vault/logs`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(d => setLogs(Array.isArray(d) ? d : []))
      .catch(() => setLogs([]))
      .finally(() => setLoading(false));
  }, []);

  // Load file when date selected
  const loadEntry = useCallback((entry: LogEntry) => {
    setSelected(entry.date);
    fetch(`${API}/vault/file/${encodeURIComponent(entry.path)}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(d => { setFile(d); contentRef.current?.scrollTo(0, 0); })
      .catch(() => setFile(null));
  }, []);

  // Filter and group by month
  const filtered = useMemo(() => {
    if (!search) return logs;
    const q = search.toLowerCase();
    return logs.filter(l => l.date.includes(q) || l.title.toLowerCase().includes(q));
  }, [logs, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, LogEntry[]>();
    for (const entry of filtered) {
      const month = entry.date.slice(0, 7);
      const arr = map.get(month);
      if (arr) arr.push(entry); else map.set(month, [entry]);
    }
    return map;
  }, [filtered]);

  const formatMonth = (ym: string) => {
    const [y, m] = ym.split('-');
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${months[parseInt(m, 10) - 1]} ${y}`;
  };

  const formatDay = (date: string) => {
    const d = new Date(date + 'T00:00:00');
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    return `${days[d.getDay()]} ${d.getDate()}`;
  };

  /* ── Search bar shared component ── */
  const searchBar = (
    <div className="flex items-center gap-2 px-3 py-2.5 rounded-[7px] bg-bg-tertiary border border-border-secondary">
      <Search className="w-4 h-4 text-text-quaternary shrink-0" />
      <input
        type="text"
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="Search logs..."
        className="flex-1 text-[13px] bg-transparent text-text placeholder:text-text-quaternary outline-none"
      />
      {search && (
        <button onClick={() => setSearch('')} className="p-0.5 cursor-pointer hover:text-text-tertiary transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
          <X className="w-3.5 h-3.5 text-text-quaternary" />
        </button>
      )}
    </div>
  );

  /* ── Date list shared component ── */
  const dateList = (
    <>
      {loading ? (
        <div className="space-y-2 p-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-7 bg-bg-tertiary rounded-[4px] animate-pulse" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-[13px] text-text-quaternary text-center py-12 font-serif">
          {search ? `No logs matching "${search}"` : 'No log entries found'}
        </p>
      ) : (
        Array.from(grouped.entries()).map(([month, entries]) => (
          <div key={month} className="mb-4">
            <div className="type-overline text-text-quaternary px-2 py-1.5 mb-0.5">{formatMonth(month)}</div>
            {entries.map(entry => (
              <button
                key={entry.date}
                onClick={() => loadEntry(entry)}
                className={`w-full flex items-center gap-2 py-2 px-2.5 rounded-[5px] text-left cursor-pointer transition-colors ${
                  selected === entry.date
                    ? 'bg-bg-tertiary border-l-2 border-accent text-text'
                    : 'hover:bg-hover text-text-secondary'
                }`}
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <span className="text-[13px] tabular-nums w-[52px] shrink-0">{formatDay(entry.date)}</span>
                <span className="text-[12px] text-text-quaternary truncate flex-1 font-serif">
                  {entry.title !== entry.date ? entry.title : ''}
                </span>
              </button>
            ))}
          </div>
        ))
      )}
    </>
  );

  return (
    <div className="flex overflow-hidden h-full bg-bg">
      {/* Left panel: date navigator */}
      <div className="hidden md:flex w-[280px] shrink-0 border-r border-border flex-col bg-bg-panel">
        <div className="p-3 border-b border-border">
          {searchBar}
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {dateList}
        </div>
      </div>

      {/* Right panel: content viewer */}
      <div className="flex-1 flex flex-col min-w-0 bg-bg">
        {file && selected ? (
          <>
            <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-border bg-bg/80 backdrop-blur-lg">
              <div className="flex items-center gap-1.5 text-[11px] text-text-quaternary">
                <button
                  onClick={() => { setFile(null); setSelected(null); }}
                  className="hover:text-text-tertiary transition-colors cursor-pointer"
                  style={{ transitionDuration: 'var(--duration-instant)' }}
                >
                  memory
                </button>
                <ChevronRight className="w-2.5 h-2.5" />
                <span className="text-text-tertiary">{(file.frontmatter?.title as string) || selected}</span>
              </div>
            </div>
            <div ref={contentRef} className="flex-1 overflow-y-auto">
              <div className="max-w-[720px] mx-auto px-4 sm:px-6 py-6 sm:py-8">
                <h1 className="text-[24px] font-[700] text-text tracking-[-0.025em] leading-tight mb-2">
                  {(file.frontmatter?.title as string) || selected}
                </h1>
                {file.frontmatter?.date && (
                  <p className="text-[12px] text-text-quaternary mb-6 font-serif">{String(file.frontmatter.date)}</p>
                )}
                <pre className="text-[14px] leading-[1.75] text-text-secondary whitespace-pre-wrap font-serif">{file.content}</pre>
              </div>
            </div>
          </>
        ) : (
          <>
            {/* Mobile: show date list inline */}
            <div className="md:hidden flex-1 overflow-y-auto bg-bg">
              <div className="p-3 border-b border-border">
                {searchBar}
              </div>
              <div className="p-2">
                {dateList}
              </div>
            </div>
            {/* Desktop: empty state */}
            <div className="hidden md:flex flex-1 flex-col items-center justify-center text-center bg-bg">
              <BookOpen className="w-10 h-10 text-text-quaternary mb-3 opacity-20" />
              <h2 className="text-[17px] font-[600] text-text mb-1">Memory</h2>
              <p className="text-[13px] text-text-tertiary max-w-[320px] font-serif">
                Select a date from the sidebar to read that day's log
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

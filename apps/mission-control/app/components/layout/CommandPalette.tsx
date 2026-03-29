'use client';

import { useEffect, useRef, useState, useCallback, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useRouter } from 'next/navigation';
import {
  Search,
  CheckSquare,
  Bot,
  FileText,
  ShieldCheck,
  Scale,
  Calendar,
  FolderKanban,
  Brain,
  Library,
  Users,
  Building2,
  Network,
  Activity,
  Radar,
  GitBranch,
  MessageCircle,
  Zap,
  FileSearch,
  type LucideIcon,
} from 'lucide-react';
import { useUIStore } from '@/store/ui';
import { useWork, type Task } from '@/hooks/useWork';

interface NavigationEntry {
  label: string;
  href: string;
  icon: LucideIcon;
}

interface SearchResult {
  title: string;
  path: string;
  collection: string;
  snippet: string;
  score: number;
}

const SCREENS: NavigationEntry[] = [
  { label: 'Home', href: '/home', icon: Zap },
  { label: 'Tasks', href: '/tasks', icon: CheckSquare },
  { label: 'Agents', href: '/agents', icon: Bot },
  { label: 'System', href: '/system', icon: Activity },
  { label: 'Projects', href: '/projects', icon: FolderKanban },
  { label: 'Calendar', href: '/calendar', icon: Calendar },
  { label: 'Docs', href: '/docs', icon: Library },
  { label: 'Memory', href: '/memory', icon: Brain },
  { label: 'Content', href: '/content', icon: FileText },
  { label: 'Team', href: '/team', icon: Network },
  { label: 'Approvals', href: '/approvals', icon: ShieldCheck },
  { label: 'Pipeline', href: '/pipeline', icon: GitBranch },
  { label: 'Radar', href: '/radar', icon: Radar },
  { label: 'Council', href: '/council', icon: Scale },
  { label: 'Office', href: '/office', icon: Building2 },
  { label: 'People', href: '/people', icon: Users },
  { label: 'Feedback', href: '/feedback', icon: MessageCircle },
];

type ResultItem = { type: 'screen'; entry: NavigationEntry }
  | { type: 'task'; task: Task }
  | { type: 'vault'; result: SearchResult };

export default function CommandPalette() {
  const open = useUIStore((s) => s.commandPaletteOpen);
  const setOpen = useUIStore((s) => s.setCommandPaletteOpen);
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette);

  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const { data: workData } = useWork();

  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [vaultResults, setVaultResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);

  // Build combined results
  const q = query.trim().toLowerCase();
  const items: ResultItem[] = [];

  // Screen navigation
  const filteredScreens = q
    ? SCREENS.filter(s => s.label.toLowerCase().includes(q))
    : SCREENS.slice(0, 6);
  filteredScreens.forEach(entry => items.push({ type: 'screen', entry }));

  // Task search (in-memory, instant)
  if (q.length >= 2 && workData?.tasks) {
    const matchedTasks = workData.tasks
      .filter(t => t.status !== 'done' && t.status !== 'cancelled')
      .filter(t => t.title.toLowerCase().includes(q))
      .slice(0, 5);
    matchedTasks.forEach(task => items.push({ type: 'task', task }));
  }

  // Vault search results (from QMD, async)
  vaultResults.forEach(result => items.push({ type: 'vault', result }));

  // Debounced QMD search
  useEffect(() => {
    if (!open || q.length < 3) {
      setVaultResults([]);
      return;
    }

    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const { Command } = await import('@tauri-apps/plugin-shell');
        const output = await Command.create('qmd', ['query', q, '-n', '8', '--json']).execute();
        if (output.code === 0 && output.stdout) {
          const parsed = JSON.parse(output.stdout);
          const results = Array.isArray(parsed) ? parsed : parsed.results || [];
          setVaultResults(results.slice(0, 5).map((r: Record<string, unknown>) => ({
            title: r.title || r.path || 'Untitled',
            path: r.path || '',
            collection: r.collection || '',
            snippet: r.snippet || r.context || '',
            score: r.score || 0,
          })));
        }
      } catch {
        // QMD unavailable
      } finally {
        setSearching(false);
      }
    }, 500);

    return () => clearTimeout(timer);
  }, [q, open]);

  // Reset state when opening
  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIndex(0);
      setVaultResults([]);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Clamp selectedIndex
  useEffect(() => {
    setSelectedIndex(prev => Math.min(prev, Math.max(items.length - 1, 0)));
  }, [items.length]);

  // Global Cmd+K
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey && e.key === 'k') {
        e.preventDefault();
        toggleCommandPalette();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [toggleCommandPalette]);

  const activate = useCallback(
    (item: ResultItem) => {
      if (item.type === 'screen') {
        router.push(item.entry.href);
      } else if (item.type === 'task') {
        router.push('/tasks');
      } else if (item.type === 'vault') {
        router.push('/docs');
      }
      setOpen(false);
    },
    [router, setOpen]
  );

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => (prev + 1) % Math.max(items.length, 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => (prev - 1 + items.length) % Math.max(items.length, 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (items[selectedIndex]) activate(items[selectedIndex]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    }
  };

  if (!open) return null;

  // Group items by type for section headers
  let screenIdx = 0, taskIdx = 0, vaultIdx = 0;
  let currentGlobalIdx = 0;

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center pt-[18vh]"
      onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}
    >
      <div className="max-w-[640px] w-full mx-4 bg-bg-secondary border border-border-secondary rounded-[10px] shadow-high overflow-hidden">
        {/* Search input */}
        <div className="border-b border-border flex items-center px-4">
          <Search className="w-4 h-4 text-text-quaternary shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelectedIndex(0); }}
            onKeyDown={handleKeyDown}
            placeholder="Search tasks, docs, or navigate..."
            className="flex-1 h-12 border-none outline-none text-[14px] bg-transparent text-text placeholder:text-text-quaternary ml-3"
          />
          {searching && (
            <span className="text-[10px] text-text-quaternary">searching...</span>
          )}
        </div>

        {/* Results */}
        <div className="max-h-[400px] overflow-y-auto py-2">
          {/* Screens section */}
          {filteredScreens.length > 0 && (
            <>
              <div className="px-4 py-1.5">
                <span className="text-[10px] font-[590] uppercase text-text-quaternary tracking-[0.06em]">Navigate</span>
              </div>
              {filteredScreens.map((entry) => {
                const idx = currentGlobalIdx++;
                const Icon = entry.icon;
                return (
                  <button
                    key={entry.href}
                    type="button"
                    onClick={() => activate({ type: 'screen', entry })}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    className={`w-[calc(100%-16px)] mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer transition-colors text-left ${
                      idx === selectedIndex ? 'bg-hover text-text' : 'text-text-secondary'
                    }`}
                    style={{ transitionDuration: 'var(--duration-instant)' }}
                  >
                    <Icon className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
                    <span className="text-xs font-[510]">{entry.label}</span>
                  </button>
                );
              })}
            </>
          )}

          {/* Tasks section */}
          {items.filter(i => i.type === 'task').length > 0 && (
            <>
              <div className="px-4 py-1.5 mt-1">
                <span className="text-[10px] font-[590] uppercase text-text-quaternary tracking-[0.06em]">Tasks</span>
              </div>
              {items.filter(i => i.type === 'task').map((item) => {
                if (item.type !== 'task') return null;
                const idx = currentGlobalIdx++;
                return (
                  <button
                    key={item.task.id}
                    type="button"
                    onClick={() => activate(item)}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    className={`w-[calc(100%-16px)] mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer transition-colors text-left ${
                      idx === selectedIndex ? 'bg-hover text-text' : 'text-text-secondary'
                    }`}
                    style={{ transitionDuration: 'var(--duration-instant)' }}
                  >
                    <CheckSquare className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
                    <span className="text-xs font-[510] flex-1 truncate">{item.task.title}</span>
                    <span className="text-[10px] text-text-quaternary">{item.task.project || ''}</span>
                  </button>
                );
              })}
            </>
          )}

          {/* Vault search section */}
          {vaultResults.length > 0 && (
            <>
              <div className="px-4 py-1.5 mt-1">
                <span className="text-[10px] font-[590] uppercase text-text-quaternary tracking-[0.06em]">Vault</span>
              </div>
              {vaultResults.map((result, i) => {
                const idx = currentGlobalIdx++;
                return (
                  <button
                    key={result.path + i}
                    type="button"
                    onClick={() => activate({ type: 'vault', result })}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    className={`w-[calc(100%-16px)] mx-2 min-h-[32px] flex items-center gap-3 px-3 py-1.5 rounded-[4px] cursor-pointer transition-colors text-left ${
                      idx === selectedIndex ? 'bg-hover text-text' : 'text-text-secondary'
                    }`}
                    style={{ transitionDuration: 'var(--duration-instant)' }}
                  >
                    <FileSearch className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-[510] truncate">{result.title}</div>
                      {result.snippet && (
                        <div className="text-[10px] text-text-quaternary truncate mt-0.5">{result.snippet}</div>
                      )}
                    </div>
                    <span className="text-[10px] text-text-quaternary shrink-0">{result.collection}</span>
                  </button>
                );
              })}
            </>
          )}

          {items.length === 0 && !searching && q.length > 0 && (
            <div className="px-4 py-8 text-center">
              <span className="text-xs text-text-quaternary">No results found</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

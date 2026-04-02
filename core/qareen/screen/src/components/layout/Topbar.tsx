import { useLocation } from 'react-router-dom';
import {
  Menu,
  Search,
  Sun,
  Moon,
  RefreshCw,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useUIStore } from '@/store/ui';
import { useRealtimeStore } from '@/store/realtime';

const SCREEN_NAMES: Record<string, string> = {
  '': 'Companion',
  tasks: 'Tasks',
  agents: 'Agents',
  approvals: 'Approvals',
  calendar: 'Calendar',
  projects: 'Projects',
  memory: 'Memory',
  vault: 'Vault',
  people: 'People',
  system: 'System',
  pipelines: 'Pipelines',
  analytics: 'Analytics',
  config: 'Config',
  channels: 'Channels',
  chief: 'Chief',
  meeting: 'Meeting',
};

function deriveTitle(pathname: string): string {
  const segment = pathname.split('/').filter(Boolean)[0] ?? '';
  return SCREEN_NAMES[segment] ?? segment.charAt(0).toUpperCase() + segment.slice(1);
}

export default function Topbar() {
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const setCommandPaletteOpen = useUIStore((s) => s.setCommandPaletteOpen);
  const queryClient = useQueryClient();
  const { pathname } = useLocation();
  const title = deriveTitle(pathname);
  const connected = useRealtimeStore((s) => s.connected);

  const [theme, setTheme] = useState<'dark' | 'light'>('dark');

  useEffect(() => {
    const saved = localStorage.getItem('qareen-theme') as 'dark' | 'light' | null;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');

    const initial = saved ?? 'dark'; // Qareen defaults to dark mode always
    setTheme(initial);
    document.documentElement.setAttribute('data-theme', initial);
    document.documentElement.classList.toggle('dark', initial === 'dark');

    function onSystemChange(e: MediaQueryListEvent) {
      if (localStorage.getItem('qareen-theme')) return;
      const next = e.matches ? 'dark' : 'light';
      setTheme(next);
      document.documentElement.setAttribute('data-theme', next);
      document.documentElement.classList.toggle('dark', next === 'dark');
    }
    mq.addEventListener('change', onSystemChange);
    return () => mq.removeEventListener('change', onSystemChange);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(prev => {
      const next = prev === 'dark' ? 'light' : 'dark';
      localStorage.setItem('qareen-theme', next);
      document.documentElement.setAttribute('data-theme', next);
      document.documentElement.classList.toggle('dark', next === 'dark');
      return next;
    });
  }, []);

  return (
    <header className="h-12 shrink-0 flex items-center px-4 gap-2 bg-bg border-b border-border select-none z-[100]">
      {/* Sidebar toggle */}
      <button
        type="button"
        onClick={toggleSidebar}
        className="w-7 h-7 flex items-center justify-center rounded-sm hover:bg-hover text-text-tertiary hover:text-text-secondary transition-colors duration-100"
        aria-label="Toggle sidebar"
      >
        <Menu className="w-4 h-4" />
      </button>

      {/* Page title — left-aligned */}
      <h1
        className="text-sm font-semibold text-text tracking-[-0.01em] select-none"
        style={{ viewTransitionName: 'page-title' }}
      >
        {title}
      </h1>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Right section */}
      <div className="flex items-center gap-1">
        {/* Connection status */}
        <div className="flex items-center gap-1.5 mr-2">
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              connected ? 'bg-green' : 'bg-text-quaternary'
            }`}
          />
          <span className="text-[10px] text-text-quaternary font-[510]">
            {connected ? 'Live' : 'Offline'}
          </span>
        </div>

        {/* Search / Cmd+K */}
        <button
          type="button"
          onClick={() => setCommandPaletteOpen(true)}
          className="h-7 flex items-center gap-2 px-2.5 rounded-[5px] bg-bg-secondary border border-border text-text-tertiary hover:text-text-secondary hover:border-border-secondary transition-colors duration-100"
          aria-label="Search"
        >
          <Search className="w-3.5 h-3.5" />
          <span className="text-[11px] font-[510]">Search</span>
          <kbd className="text-[10px] font-[510] text-text-quaternary bg-bg-tertiary px-1 py-0.5 rounded-[3px] ml-1">
            {'\u2318'}K
          </kbd>
        </button>

        {/* Theme toggle */}
        <button
          type="button"
          onClick={toggleTheme}
          className="w-8 h-8 flex items-center justify-center rounded-sm text-text-secondary hover:text-text hover:bg-hover transition-colors duration-100"
          aria-label="Toggle theme"
        >
          {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>

        {/* Refresh */}
        <button
          type="button"
          onClick={() => queryClient.invalidateQueries()}
          className="w-8 h-8 flex items-center justify-center rounded-sm text-text-secondary hover:text-text hover:bg-hover transition-colors duration-100"
          aria-label="Refresh all data"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}

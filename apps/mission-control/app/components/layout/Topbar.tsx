'use client';

import { usePathname } from 'next/navigation';
import {
  Menu,
  Search,
  Settings,
  RefreshCw,
  Sun,
  Moon,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { useUIStore } from '@/store/ui';

/** Map pathname segments to display names. */
const SCREEN_NAMES: Record<string, string> = {
  home: 'Home',
  chief: 'Chief',
  tasks: 'Tasks',
  agents: 'Agents',
  content: 'Content',
  approvals: 'Approvals',
  council: 'Council',
  calendar: 'Calendar',
  projects: 'Projects',
  memory: 'Memory',
  docs: 'Docs',
  people: 'People',
  office: 'Office',
  team: 'Team',
  system: 'System',
  radar: 'Radar',
  pipeline: 'Pipeline',
  feedback: 'Feedback',
};

function deriveTitle(pathname: string): string {
  const segment = pathname.split('/').filter(Boolean)[0] ?? 'home';
  return SCREEN_NAMES[segment] ?? segment.charAt(0).toUpperCase() + segment.slice(1);
}

export default function Topbar() {
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const setCommandPaletteOpen = useUIStore((s) => s.setCommandPaletteOpen);
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const title = deriveTitle(pathname);

  const [theme, setTheme] = useState<'dark' | 'light'>('dark');

  const handleDrag = useCallback((e: React.MouseEvent) => {
    // Only drag from the header itself or spacer, not from buttons
    const target = e.target as HTMLElement;
    if (target.closest('button') || target.closest('input') || target.closest('kbd')) return;
    e.preventDefault();
    getCurrentWindow().startDragging();
  }, []);

  useEffect(() => {
    const saved = localStorage.getItem('mc-theme') as 'dark' | 'light' | null;
    if (saved) {
      setTheme(saved);
      document.documentElement.setAttribute('data-theme', saved);
      document.documentElement.classList.toggle('dark', saved === 'dark');
    }
  }, []);

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    localStorage.setItem('mc-theme', next);
    document.documentElement.setAttribute('data-theme', next);
    document.documentElement.classList.toggle('dark', next === 'dark');
  }

  return (
    <header
      className="h-12 shrink-0 flex items-center px-4 gap-3 bg-bg border-b border-border select-none"
      onMouseDown={handleDrag}
    >
      {/* Left section — offset for traffic lights */}
      <div className="w-[54px] shrink-0" />
      <button
        type="button"
        onClick={toggleSidebar}
        className="w-8 h-8 flex items-center justify-center rounded-sm hover:bg-hover text-text-tertiary hover:text-text-secondary transition-colors duration-100"
        aria-label="Toggle sidebar"
      >
        <Menu className="w-4 h-4" />
      </button>

      <h1 className="text-sm font-semibold text-text tracking-[-0.01em] select-none">
        {title}
      </h1>

      {/* Right section */}
      <div className="flex-1 flex items-center justify-end gap-2">
        {/* Search trigger */}
        <button
          type="button"
          onClick={() => setCommandPaletteOpen(true)}
          className="w-[220px] h-8 flex items-center px-3 gap-2 rounded-sm border border-border-secondary bg-bg-tertiary hover:bg-bg-quaternary transition-colors duration-100"
        >
          <Search className="w-3.5 h-3.5 text-text-tertiary shrink-0" />
          <span className="text-xs text-text-tertiary flex-1 text-left">
            Search...
          </span>
          <kbd className="text-[11px] font-mono leading-none bg-bg-quaternary text-text-quaternary rounded-xs px-1.5 py-0.5">
            ⌘K
          </kbd>
        </button>

        {/* Theme toggle */}
        <button
          type="button"
          onClick={toggleTheme}
          className="w-8 h-8 flex items-center justify-center rounded-sm text-text-tertiary hover:text-text-secondary hover:bg-hover transition-colors duration-100"
          aria-label="Toggle theme"
        >
          {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>

        {/* Settings */}
        <button
          type="button"
          className="w-8 h-8 flex items-center justify-center rounded-sm text-text-tertiary hover:text-text-secondary hover:bg-hover transition-colors duration-100"
          aria-label="Settings"
        >
          <Settings className="w-4 h-4" />
        </button>

        {/* Refresh */}
        <button
          type="button"
          onClick={() => queryClient.invalidateQueries()}
          className="w-8 h-8 flex items-center justify-center rounded-sm text-text-tertiary hover:text-text-secondary hover:bg-hover transition-colors duration-100"
          aria-label="Refresh all data"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}

import { useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Command } from 'cmdk';
import {
  Search,
  CheckSquare,
  Bot,
  ShieldCheck,
  Calendar,
  FolderKanban,
  Brain,
  Library,
  Users,
  Activity,
  GitBranch,
  MessageCircle,
  BarChart3,
  Settings,
  Mic,
  Plus,
  Sun,
  RefreshCw,
  type LucideIcon,
} from 'lucide-react';
import { useUIStore } from '@/store/ui';

interface NavEntry {
  label: string;
  href: string;
  icon: LucideIcon;
  keywords?: string[];
}

const NAV_ITEMS: NavEntry[] = [
  { label: 'Go to Companion', href: '/', icon: Mic, keywords: ['home', 'chat', 'voice'] },
  { label: 'Go to Tasks', href: '/tasks', icon: CheckSquare, keywords: ['todo', 'work'] },
  { label: 'Go to Projects', href: '/projects', icon: FolderKanban, keywords: ['project'] },
  { label: 'Go to Calendar', href: '/calendar', icon: Calendar, keywords: ['schedule', 'date'] },
  { label: 'Go to Vault', href: '/vault', icon: Library, keywords: ['docs', 'notes', 'knowledge'] },
  { label: 'Go to Memory', href: '/memory', icon: Brain, keywords: ['recall', 'remember'] },
  { label: 'Go to Agents', href: '/agents', icon: Bot, keywords: ['agent', 'ai'] },
  { label: 'Go to Approvals', href: '/approvals', icon: ShieldCheck, keywords: ['approve', 'pending'] },
  { label: 'Go to System', href: '/system', icon: Activity, keywords: ['health', 'services'] },
  { label: 'Go to Pipelines', href: '/pipelines', icon: GitBranch, keywords: ['pipeline', 'flow'] },
  { label: 'Go to Analytics', href: '/analytics', icon: BarChart3, keywords: ['stats', 'chart'] },
  { label: 'Go to People', href: '/people', icon: Users, keywords: ['contacts', 'person'] },
  { label: 'Go to Config', href: '/config', icon: Settings, keywords: ['settings', 'preferences'] },
  { label: 'Go to Channels', href: '/channels', icon: MessageCircle, keywords: ['telegram', 'messages'] },
];

export default function CommandPalette() {
  const open = useUIStore((s) => s.commandPaletteOpen);
  const setOpen = useUIStore((s) => s.setCommandPaletteOpen);
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Global Cmd+K listener
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

  const runAndClose = useCallback(
    (fn: () => void) => {
      fn();
      setOpen(false);
    },
    [setOpen],
  );

  const toggleTheme = useCallback(() => {
    const current = document.documentElement.getAttribute('data-theme') ?? 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('qareen-theme', next);
    document.documentElement.setAttribute('data-theme', next);
    document.documentElement.classList.toggle('dark', next === 'dark');
  }, []);

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command palette"
      loop
      overlayClassName="command-overlay"
      contentClassName="command-content"
    >
      <div className="flex items-center border-b border-border px-4">
        <Search className="w-4 h-4 text-text-quaternary shrink-0" />
        <Command.Input
          placeholder="Type a command or search..."
          className="flex-1 h-12 border-none outline-none text-[14px] bg-transparent text-text placeholder:text-text-quaternary ml-3"
        />
      </div>

      <Command.List className="max-h-[340px] overflow-y-auto py-2">
        <Command.Empty className="px-4 py-8 text-center text-xs text-text-quaternary">
          No results found.
        </Command.Empty>

        {/* Navigation */}
        <Command.Group
          heading="Navigation"
          className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:type-overline [&_[cmdk-group-heading]]:text-text-quaternary"
        >
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <Command.Item
                key={item.href}
                value={item.label}
                keywords={item.keywords}
                onSelect={() => runAndClose(() => navigate(item.href))}
                className="mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer text-text-secondary data-[selected=true]:bg-bg-tertiary data-[selected=true]:text-text transition-colors"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <Icon className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
                <span className="text-xs font-[510]">{item.label}</span>
              </Command.Item>
            );
          })}
        </Command.Group>

        {/* Actions */}
        <Command.Group
          heading="Actions"
          className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:mt-1 [&_[cmdk-group-heading]]:type-overline [&_[cmdk-group-heading]]:text-text-quaternary"
        >
          <Command.Item
            value="New Task"
            keywords={['create', 'add', 'todo']}
            onSelect={() => runAndClose(() => navigate('/tasks?new=true'))}
            className="mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer text-text-secondary data-[selected=true]:bg-bg-tertiary data-[selected=true]:text-text transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <Plus className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <span className="text-xs font-[510]">New Task</span>
          </Command.Item>

          <Command.Item
            value="Toggle Theme"
            keywords={['dark', 'light', 'mode', 'appearance']}
            onSelect={() => runAndClose(toggleTheme)}
            className="mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer text-text-secondary data-[selected=true]:bg-bg-tertiary data-[selected=true]:text-text transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <Sun className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <span className="text-xs font-[510]">Toggle Theme</span>
          </Command.Item>

          <Command.Item
            value="Refresh Data"
            keywords={['reload', 'invalidate', 'fetch']}
            onSelect={() => runAndClose(() => queryClient.invalidateQueries())}
            className="mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer text-text-secondary data-[selected=true]:bg-bg-tertiary data-[selected=true]:text-text transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <RefreshCw className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <span className="text-xs font-[510]">Refresh Data</span>
          </Command.Item>
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}

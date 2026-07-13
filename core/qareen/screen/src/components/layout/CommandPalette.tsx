import { useEffect, useCallback, useMemo } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Command } from 'cmdk';
import {
  Search,
  CheckSquare,
  Bot,
  ShieldCheck,
  Calendar,
  FolderKanban,
  Library,
  Users,
  Activity,
  BarChart3,
  Settings,
  Mic,
  Plus,
  Sun,
  RefreshCw,
  Columns3,
  List,
  LayoutGrid,
  Filter,
  Radar,
  type LucideIcon,
} from 'lucide-react';
import { useUIStore } from '@/store/ui';
import { useWork, type Task } from '@/hooks/useWork';

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
  { label: 'Go to Agents', href: '/agents', icon: Bot, keywords: ['agent', 'ai'] },
  { label: 'Go to Approvals', href: '/approvals', icon: ShieldCheck, keywords: ['approve', 'pending'] },
  { label: 'Go to Sentinel', href: '/sentinel', icon: Radar, keywords: ['sentinel', 'agent', 'autonomous', 'monitor', 'triggers'] },
  { label: 'Go to System', href: '/system', icon: Activity, keywords: ['health', 'services'] },
  { label: 'Go to Analytics', href: '/analytics', icon: BarChart3, keywords: ['stats', 'chart'] },
  { label: 'Go to People', href: '/people', icon: Users, keywords: ['contacts', 'person'] },
  { label: 'Go to Settings', href: '/settings', icon: Settings, keywords: ['settings', 'preferences'] },
];

export default function CommandPalette() {
  const open = useUIStore((s) => s.commandPaletteOpen);
  const setOpen = useUIStore((s) => s.setCommandPaletteOpen);
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette);
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { data: workData } = useWork();

  const isOnTasks = location.pathname === '/work' || location.pathname === '/tasks';

  // All tasks flattened for search — include subtasks so everything is findable
  const activeTasks = useMemo(() => {
    if (!workData?.tasks) return [];
    const flat: Task[] = [];
    function flatten(list: Task[]) {
      for (const t of list) {
        if (t.status !== 'done' && t.status !== 'cancelled') flat.push(t);
        if (t.subtasks) flatten(t.subtasks);
      }
    }
    flatten(workData.tasks);
    return flat;
  }, [workData?.tasks]);

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

          <Command.Item
            value="Stream View"
            keywords={['view', 'list', 'stream', 'grouped']}
            onSelect={() => runAndClose(() => navigate('/work?tab=tasks&view=stream'))}
            className="mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer text-text-secondary data-[selected=true]:bg-bg-tertiary data-[selected=true]:text-text transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <List className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <span className="text-xs font-[510]">Stream View</span>
          </Command.Item>

          <Command.Item
            value="Board View"
            keywords={['kanban', 'columns', 'board']}
            onSelect={() => runAndClose(() => navigate('/work?tab=tasks&view=board'))}
            className="mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer text-text-secondary data-[selected=true]:bg-bg-tertiary data-[selected=true]:text-text transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <Columns3 className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <span className="text-xs font-[510]">Board View</span>
          </Command.Item>

          <Command.Item
            value="List View"
            keywords={['table', 'database', 'spreadsheet']}
            onSelect={() => runAndClose(() => navigate('/work?tab=tasks&view=list'))}
            className="mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer text-text-secondary data-[selected=true]:bg-bg-tertiary data-[selected=true]:text-text transition-colors"
            style={{ transitionDuration: 'var(--duration-instant)' }}
          >
            <LayoutGrid className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <span className="text-xs font-[510]">List View</span>
          </Command.Item>
        </Command.Group>

        {/* Tasks — searchable, all active */}
        {activeTasks.length > 0 && (
          <Command.Group
            heading="Tasks"
            className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:mt-1 [&_[cmdk-group-heading]]:type-overline [&_[cmdk-group-heading]]:text-text-quaternary"
          >
            {activeTasks.map((task) => (
              <Command.Item
                key={task.id}
                value={`${task.title} ${task.project ?? ''}`}
                keywords={[task.id, task.project ?? '', task.status]}
                onSelect={() => runAndClose(() => navigate(`/work?tab=tasks&open=${task.id}`))}
                className="mx-2 h-8 flex items-center gap-3 px-3 rounded-[4px] cursor-pointer text-text-secondary data-[selected=true]:bg-bg-tertiary data-[selected=true]:text-text transition-colors"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                <CheckSquare className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
                <span className="text-xs font-[510] truncate flex-1">{task.title}</span>
                {task.project && (
                  <span className="text-[10px] text-text-quaternary shrink-0">{task.project}</span>
                )}
              </Command.Item>
            ))}
          </Command.Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
}

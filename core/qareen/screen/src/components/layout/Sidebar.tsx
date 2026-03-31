import { useLocation } from 'react-router-dom';
import TransitionLink from '@/components/primitives/TransitionLink';
import {
  CheckSquare, Bot, ShieldCheck, Calendar,
  FolderKanban, Brain, Library, Users,
  Activity, GitBranch, MessageCircle,
  BarChart3, Settings, Mic,
  type LucideIcon,
} from 'lucide-react';
import { useUIStore } from '@/store/ui';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { createPortal } from 'react-dom';

const PREFETCH_MAP: Record<string, string[]> = {
  '/tasks': ['work'],
  '/': ['work', 'services'],
  '/agents': ['agents'],
  '/system': ['services', 'crons'],
};

interface NavItem { label: string; href: string; icon: LucideIcon; live?: boolean; }
interface NavGroup { label: string; items: NavItem[]; }

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Focus',
    items: [
      { label: 'Companion', href: '/', icon: Mic, live: true },
      { label: 'Tasks', href: '/tasks', icon: CheckSquare, live: true },
      { label: 'Projects', href: '/projects', icon: FolderKanban, live: true },
      { label: 'Calendar', href: '/calendar', icon: Calendar, live: true },
    ],
  },
  {
    label: 'Knowledge',
    items: [
      { label: 'Vault', href: '/vault', icon: Library, live: true },
      { label: 'Memory', href: '/memory', icon: Brain, live: true },
    ],
  },
  {
    label: 'Agents',
    items: [
      { label: 'Agents', href: '/agents', icon: Bot, live: true },
      { label: 'Approvals', href: '/approvals', icon: ShieldCheck, live: true },
    ],
  },
  {
    label: 'System',
    items: [
      { label: 'System', href: '/system', icon: Activity, live: true },
      { label: 'Pipelines', href: '/pipelines', icon: GitBranch, live: true },
    ],
  },
  {
    label: 'More',
    items: [
      { label: 'Analytics', href: '/analytics', icon: BarChart3, live: true },
      { label: 'People', href: '/people', icon: Users, live: true },
      { label: 'Config', href: '/config', icon: Settings, live: true },
      { label: 'Channels', href: '/channels', icon: MessageCircle, live: true },
    ],
  },
];

export default function Sidebar() {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const location = useLocation();
  const pathname = location.pathname;
  const queryClient = useQueryClient();
  const [tooltip, setTooltip] = useState<{ label: string; x: number; y: number } | null>(null);

  const handleHover = useCallback((href: string) => {
    const keys = PREFETCH_MAP[href];
    if (keys) {
      keys.forEach(key => {
        queryClient.prefetchQuery({ queryKey: [key], staleTime: 15_000 });
      });
    }
  }, [queryClient]);

  return (
    <aside
      className="shrink-0 h-full flex flex-col bg-bg-panel border-r border-border overflow-hidden"
      style={{ width: sidebarOpen ? 200 : 52, transition: 'width 220ms var(--ease-in-out)' }}
    >
      <nav className="flex-1 overflow-y-auto pt-2.5 pb-3">
        {NAV_GROUPS.map((group, groupIdx) => (
          <div key={group.label} className={groupIdx === 0 ? '' : 'mt-4'}>
            {sidebarOpen ? (
              <div className="px-4 mb-1">
                <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">{group.label}</span>
              </div>
            ) : groupIdx > 0 ? (
              <div className="mx-3 mb-1.5 h-px bg-border" />
            ) : null}
            <div className="space-y-px">
              {group.items.map((item) => {
                const isActive = item.href === '/'
                  ? pathname === '/'
                  : pathname === item.href || pathname.startsWith(item.href + '/');
                const Icon = item.icon;
                const isStub = !item.live;
                return (
                  <div
                    key={item.href}
                    onMouseEnter={(e) => {
                      handleHover(item.href);
                      if (!sidebarOpen) {
                        const rect = e.currentTarget.getBoundingClientRect();
                        setTooltip({ label: item.label, x: rect.right + 8, y: rect.top + rect.height / 2 });
                      }
                    }}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    <TransitionLink
                      href={item.href}
                      className={`flex items-center gap-2.5 mx-2 px-2 h-7 rounded-[4px] transition-colors
                        ${isStub ? 'opacity-[0.35] pointer-events-auto' : ''}
                        ${isActive ? 'bg-active text-text' : isStub ? 'text-text-quaternary' : 'text-text-tertiary hover:bg-hover hover:text-text-secondary'}`}
                      style={{ transitionDuration: 'var(--duration-instant)' }}
                    >
                      <Icon className="w-3.5 h-3.5 shrink-0" />
                      {sidebarOpen && (
                        <span className={`text-xs truncate ${isActive ? 'font-[590]' : 'font-[510]'}`}>{item.label}</span>
                      )}
                    </TransitionLink>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      {tooltip && createPortal(
        <div
          className="fixed px-2.5 py-1 rounded-[5px] bg-bg-tertiary text-text text-xs font-[510] whitespace-nowrap z-[1100] pointer-events-none shadow-medium"
          style={{ left: tooltip.x, top: tooltip.y, transform: 'translateY(-50%)' }}
        >{tooltip.label}</div>,
        document.body
      )}
    </aside>
  );
}

'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
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
  type LucideIcon,
} from 'lucide-react';
import { useUIStore } from '@/store/ui';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';

// Map routes to the query keys they need
const PREFETCH_MAP: Record<string, string[]> = {
  '/tasks': ['work'],
  '/home': ['work', 'services'],
  '/agents': ['agents'],
  '/system': ['services', 'crons'],
};

interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  live?: boolean;  // true = working screen, false = stub
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Focus',
    items: [
      { label: 'Chief', href: '/chief', icon: Zap, live: true },
      { label: 'Tasks', href: '/tasks', icon: CheckSquare, live: true },
      { label: 'Projects', href: '/projects', icon: FolderKanban },
      { label: 'Calendar', href: '/calendar', icon: Calendar },
    ],
  },
  {
    label: 'Knowledge',
    items: [
      { label: 'Docs', href: '/docs', icon: Library },
      { label: 'Memory', href: '/memory', icon: Brain },
      { label: 'Content', href: '/content', icon: FileText },
    ],
  },
  {
    label: 'Agents',
    items: [
      { label: 'Agents', href: '/agents', icon: Bot, live: true },
      { label: 'Team', href: '/team', icon: Network },
      { label: 'Approvals', href: '/approvals', icon: ShieldCheck },
    ],
  },
  {
    label: 'System',
    items: [
      { label: 'System', href: '/system', icon: Activity, live: true },
      { label: 'Pipeline', href: '/pipeline', icon: GitBranch },
    ],
  },
  {
    label: 'More',
    items: [
      { label: 'Radar', href: '/radar', icon: Radar },
      { label: 'Council', href: '/council', icon: Scale },
      { label: 'Office', href: '/office', icon: Building2 },
      { label: 'People', href: '/people', icon: Users },
      { label: 'Feedback', href: '/feedback', icon: MessageCircle },
    ],
  },
];

export default function Sidebar() {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const pathname = usePathname();
  const queryClient = useQueryClient();

  const handleHover = useCallback((href: string) => {
    const keys = PREFETCH_MAP[href];
    if (keys) {
      keys.forEach(key => {
        // Prefetch if not already cached or stale
        queryClient.prefetchQuery({
          queryKey: [key],
          staleTime: 15_000,
        });
      });
    }
  }, [queryClient]);

  return (
    <aside
      className="shrink-0 flex flex-col bg-bg-panel border-r border-border overflow-hidden"
      style={{
        width: sidebarOpen ? 224 : 52,
        transition: 'width 220ms var(--ease-in-out)',
      }}
    >
      {/* Traffic light padding — draggable for window move */}
      <div
        className="h-12 shrink-0 select-none"
        onMouseDown={(e) => { e.preventDefault(); getCurrentWindow().startDragging(); }}
      />

      {/* Operator profile — doubles as Home button */}
      <Link
        href="/home"
        className={`mx-2 px-2 py-2 rounded-[5px] mb-2 block transition-colors ${
          pathname === '/home'
            ? 'bg-active'
            : 'hover:bg-hover'
        }`}
        style={{ transitionDuration: 'var(--duration-instant)' }}
      >
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-full bg-bg-tertiary flex items-center justify-center text-[11px] font-[590] text-text shrink-0">
            H
          </div>
          {sidebarOpen && (
            <div className="min-w-0">
              <div className="text-[13px] font-[590] text-text truncate">Hisham</div>
              <div className="text-[10px] text-text-quaternary truncate">AOS</div>
            </div>
          )}
        </div>
      </Link>

      {/* Navigation groups */}
      <nav className="flex-1 overflow-y-auto pb-3">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mt-4 first:mt-2">
            {/* Group label */}
            {sidebarOpen && (
              <div className="px-4 mb-1">
                <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
                  {group.label}
                </span>
              </div>
            )}

            {/* Items */}
            <div className="space-y-px">
              {group.items.map((item) => {
                const isActive =
                  pathname === item.href ||
                  pathname.startsWith(item.href + '/');
                const Icon = item.icon;
                const isStub = !item.live;

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onMouseEnter={() => handleHover(item.href)}
                    className={`
                      flex items-center gap-2.5 mx-2 px-2 h-7 rounded-[4px]
                      transition-colors
                      ${isStub
                        ? 'opacity-[0.35] pointer-events-auto'
                        : ''
                      }
                      ${isActive
                        ? 'bg-active text-text'
                        : isStub
                          ? 'text-text-quaternary'
                          : 'text-text-tertiary hover:bg-hover hover:text-text-secondary'
                      }
                    `}
                    style={{ transitionDuration: 'var(--duration-instant)' }}
                  >
                    <Icon className="w-3.5 h-3.5 shrink-0" />
                    {sidebarOpen && (
                      <span className={`text-xs truncate ${isActive ? 'font-[590]' : 'font-[510]'}`}>
                        {item.label}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  );
}

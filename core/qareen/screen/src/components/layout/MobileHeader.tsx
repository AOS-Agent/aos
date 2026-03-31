import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Link } from 'react-router-dom';
import {
  Menu, X,
  CheckSquare, Bot, ShieldCheck, Calendar,
  FolderKanban, Brain, Library, Users,
  Activity, GitBranch, MessageCircle, BarChart3, Settings, Mic,
  type LucideIcon,
} from 'lucide-react';

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

interface NavItem { label: string; href: string; icon: LucideIcon; live?: boolean; }
interface NavGroup { label: string; items: NavItem[]; }

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Focus',
    items: [
      { label: 'Companion', href: '/', icon: Mic, live: true },
      { label: 'Tasks', href: '/tasks', icon: CheckSquare, live: true },
      { label: 'Projects', href: '/projects', icon: FolderKanban },
      { label: 'Calendar', href: '/calendar', icon: Calendar },
    ],
  },
  {
    label: 'Knowledge',
    items: [
      { label: 'Vault', href: '/vault', icon: Library },
      { label: 'Memory', href: '/memory', icon: Brain },
    ],
  },
  {
    label: 'Agents',
    items: [
      { label: 'Agents', href: '/agents', icon: Bot, live: true },
      { label: 'Approvals', href: '/approvals', icon: ShieldCheck },
    ],
  },
  {
    label: 'System',
    items: [
      { label: 'System', href: '/system', icon: Activity, live: true },
      { label: 'Pipelines', href: '/pipelines', icon: GitBranch },
    ],
  },
  {
    label: 'More',
    items: [
      { label: 'Analytics', href: '/analytics', icon: BarChart3 },
      { label: 'People', href: '/people', icon: Users },
      { label: 'Config', href: '/config', icon: Settings },
      { label: 'Channels', href: '/channels', icon: MessageCircle },
    ],
  },
];

function deriveTitle(pathname: string): string {
  const segment = pathname.split('/').filter(Boolean)[0] ?? '';
  return SCREEN_NAMES[segment] ?? segment.charAt(0).toUpperCase() + segment.slice(1);
}

export default function MobileHeader() {
  const { pathname } = useLocation();
  const title = deriveTitle(pathname);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <header
        className="md:hidden shrink-0 flex items-end justify-between
          px-4 pb-2.5
          bg-bg/80 backdrop-blur-xl
          border-b border-border
          z-[90]"
        style={{
          height: 'calc(44px + env(safe-area-inset-top))',
          paddingTop: 'env(safe-area-inset-top)',
        }}
      >
        <button
          onClick={() => setMenuOpen(true)}
          className="p-1 -ml-1 rounded-md active:bg-hover"
        >
          <Menu className="w-5 h-5 text-text-secondary" />
        </button>
        <h1 className="text-[15px] font-[600] text-text tracking-[-0.01em] select-none leading-none">
          {title}
        </h1>
        <div className="w-5" />
      </header>

      {menuOpen && (
        <div className="fixed inset-0 z-[200] md:hidden" onClick={() => setMenuOpen(false)}>
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
          <div
            className="absolute left-0 top-0 bottom-0 w-[260px] bg-bg-panel border-r border-border shadow-2xl flex flex-col"
            style={{ paddingTop: 'env(safe-area-inset-top)' }}
            onClick={e => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-full bg-bg-tertiary flex items-center justify-center text-[11px] font-[590] text-text">
                  H
                </div>
                <div>
                  <div className="text-[13px] font-[590] text-text">Hisham</div>
                  <div className="text-[10px] text-text-quaternary">Qareen</div>
                </div>
              </div>
              <button onClick={() => setMenuOpen(false)} className="p-1.5 rounded-md hover:bg-hover active:bg-hover">
                <X className="w-4 h-4 text-text-tertiary" />
              </button>
            </div>

            {/* Nav groups */}
            <nav className="flex-1 overflow-y-auto pt-2 pb-4"
                 style={{ paddingBottom: 'calc(16px + env(safe-area-inset-bottom, 0px))' }}>
              {NAV_GROUPS.map((group, groupIdx) => (
                <div key={group.label} className={groupIdx === 0 ? '' : 'mt-3'}>
                  <div className="px-4 mb-1">
                    <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
                      {group.label}
                    </span>
                  </div>
                  <div className="space-y-px">
                    {group.items.map(item => {
                      const isActive = item.href === '/'
                        ? pathname === '/'
                        : pathname === item.href || pathname.startsWith(item.href + '/');
                      const isStub = !item.live;
                      const Icon = item.icon;
                      return (
                        <Link
                          key={item.href}
                          to={item.href}
                          onClick={() => setMenuOpen(false)}
                          className={`flex items-center gap-2.5 mx-2 px-2.5 py-2 rounded-md transition-colors
                            ${isStub ? 'opacity-[0.35]' : ''}
                            ${isActive
                              ? 'bg-active text-text'
                              : isStub
                                ? 'text-text-quaternary'
                                : 'text-text-tertiary hover:bg-hover active:bg-hover hover:text-text-secondary'
                            }`}
                        >
                          <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-text' : ''}`} />
                          <span className={`text-[13px] truncate ${isActive ? 'font-[590]' : 'font-[450]'}`}>
                            {item.label}
                          </span>
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>
          </div>
        </div>
      )}
    </>
  );
}

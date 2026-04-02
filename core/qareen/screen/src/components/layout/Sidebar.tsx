import { useLocation } from 'react-router-dom';
import TransitionLink from '@/components/primitives/TransitionLink';
import {
  CheckSquare, Bot, ShieldCheck, Calendar,
  FolderKanban, Brain, Library, Users,
  Activity, GitBranch, MessageCircle,
  BarChart3, Settings, Mic, History,
  Menu, Search, Sun, Moon, X,
  type LucideIcon,
} from 'lucide-react';
import { useUIStore } from '@/store/ui';
import { useQueryClient } from '@tanstack/react-query';
import { useRealtimeStore } from '@/store/realtime';
import { useCallback, useEffect, useState, useRef } from 'react';

// ---------------------------------------------------------------------------
// Sidebar — floating pill (collapsed) + overlay drawer (expanded).
//
// Collapsed: small translucent pill at top-left showing hamburger + page icon + name.
// Expanded: full nav slides out on top with backdrop blur. Selecting an item closes it.
// ---------------------------------------------------------------------------

interface NavItem { label: string; href: string; icon: LucideIcon; }
interface NavGroup { label: string; items: NavItem[]; }

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Focus',
    items: [
      { label: 'Companion', href: '/', icon: Mic },
      { label: 'Sessions', href: '/sessions', icon: History },
      { label: 'Tasks', href: '/tasks', icon: CheckSquare },
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
      { label: 'Agents', href: '/agents', icon: Bot },
      { label: 'Approvals', href: '/approvals', icon: ShieldCheck },
    ],
  },
  {
    label: 'System',
    items: [
      { label: 'System', href: '/system', icon: Activity },
      { label: 'Pipelines', href: '/pipelines', icon: GitBranch },
    ],
  },
  {
    label: 'More',
    items: [
      { label: 'Analytics', href: '/analytics', icon: BarChart3 },
      { label: 'People', href: '/people', icon: Users },
      { label: 'Channels', href: '/channels', icon: MessageCircle },
    ],
  },
];

// Flat lookup for current page
const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

function getCurrentPage(pathname: string): NavItem {
  const match = ALL_ITEMS.find((item) =>
    item.href === '/' ? pathname === '/' : pathname === item.href || pathname.startsWith(item.href + '/'),
  );
  return match ?? ALL_ITEMS[0];
}

export default function Sidebar() {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const setSidebarOpen = useUIStore((s) => s.setSidebarOpen);
  const setCommandPaletteOpen = useUIStore((s) => s.setCommandPaletteOpen);
  const connected = useRealtimeStore((s) => s.connected);
  const location = useLocation();
  const pathname = location.pathname;
  const queryClient = useQueryClient();
  const drawerRef = useRef<HTMLDivElement>(null);

  const currentPage = getCurrentPage(pathname);

  // Theme
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  useEffect(() => {
    const saved = localStorage.getItem('qareen-theme') as 'dark' | 'light' | null;
    const initial = saved ?? 'dark';
    setTheme(initial);
    document.documentElement.setAttribute('data-theme', initial);
    document.documentElement.classList.toggle('dark', initial === 'dark');
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark';
      localStorage.setItem('qareen-theme', next);
      document.documentElement.setAttribute('data-theme', next);
      document.documentElement.classList.toggle('dark', next === 'dark');
      return next;
    });
  }, []);

  // Close drawer on nav
  const handleNav = useCallback(
    (href: string) => {
      setSidebarOpen(false);
      // Prefetch
      const prefetchMap: Record<string, string[]> = {
        '/tasks': ['work'],
        '/': ['work', 'services'],
        '/agents': ['agents'],
        '/system': ['services', 'crons'],
      };
      const keys = prefetchMap[href];
      if (keys) keys.forEach((k) => queryClient.prefetchQuery({ queryKey: [k], staleTime: 15_000 }));
    },
    [setSidebarOpen, queryClient],
  );

  // Close on escape
  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSidebarOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [sidebarOpen, setSidebarOpen]);

  // Close on click outside
  useEffect(() => {
    if (!sidebarOpen) return;
    const onClick = (e: MouseEvent) => {
      if (drawerRef.current && !drawerRef.current.contains(e.target as Node)) {
        setSidebarOpen(false);
      }
    };
    // Delay to avoid catching the open click
    const timer = setTimeout(() => window.addEventListener('click', onClick), 50);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('click', onClick);
    };
  }, [sidebarOpen, setSidebarOpen]);

  const CurrentIcon = currentPage.icon;

  // Animate close: keep mounted during exit animation
  const [visible, setVisible] = useState(false);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    if (sidebarOpen) {
      setVisible(true);
      setClosing(false);
    } else if (visible) {
      setClosing(true);
      const timer = setTimeout(() => { setVisible(false); setClosing(false); }, 180);
      return () => clearTimeout(timer);
    }
  }, [sidebarOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleOpen = useCallback(() => setSidebarOpen(!sidebarOpen), [sidebarOpen, setSidebarOpen]);

  return (
    <>
      {/* ── Toggle pill — always visible, same position ── */}
      <div className="fixed top-3 left-3 z-[320]">
        <button
          type="button"
          onClick={toggleOpen}
          className={`
            flex items-center gap-2 h-8 pl-2 pr-3
            rounded-full
            backdrop-blur-md
            border border-border/40
            transition-all duration-150
            shadow-[0_2px_12px_rgba(0,0,0,0.3)]
            ${sidebarOpen
              ? 'bg-bg-tertiary/80 text-text hover:bg-bg-quaternary/80'
              : 'bg-bg-secondary/60 text-text-secondary hover:bg-bg-tertiary/70 hover:text-text'
            }
          `}
        >
          {sidebarOpen
            ? <X className="w-3.5 h-3.5" />
            : <Menu className="w-3.5 h-3.5 text-text-tertiary" />
          }
          <CurrentIcon className="w-3.5 h-3.5" />
          <span className="text-[12px] font-[510]">{currentPage.label}</span>
          <div className={`w-1.5 h-1.5 rounded-full ml-0.5 ${connected ? 'bg-green' : 'bg-red'}`} />
        </button>
      </div>

      {/* ── Overlay drawer (mounted while visible OR closing) ── */}
      {visible && (
        <>
          {/* Backdrop blur */}
          <div
            className="fixed inset-0 z-[300] bg-bg/40 backdrop-blur-sm"
            onClick={() => setSidebarOpen(false)}
            style={{ animation: `${closing ? 'fade-out' : 'fade-in'} 180ms ease-out forwards` }}
          />

          {/* Drawer panel */}
          <div
            ref={drawerRef}
            className="
              fixed top-0 left-0 bottom-0 z-[310]
              w-[240px] max-w-[80vw]
              bg-bg-panel/95 backdrop-blur-xl
              border-r border-border/40
              flex flex-col
              shadow-[4px_0_24px_rgba(0,0,0,0.4)]
            "
            style={{ animation: `${closing ? 'slide-out-left' : 'slide-in-left'} 180ms ease-out forwards` }}
          >
            {/* Spacer for the floating pill above */}
            <div className="pt-11" />

            {/* Nav groups */}
            <nav className="flex-1 overflow-y-auto px-2 pb-2">
              {NAV_GROUPS.map((group, groupIdx) => (
                <div key={group.label} className={groupIdx === 0 ? '' : 'mt-4'}>
                  <div className="px-2 mb-1">
                    <span className="text-[10px] font-[590] uppercase tracking-[0.06em] text-text-quaternary">
                      {group.label}
                    </span>
                  </div>
                  <div className="space-y-px">
                    {group.items.map((item) => {
                      const isActive =
                        item.href === '/'
                          ? pathname === '/'
                          : pathname === item.href || pathname.startsWith(item.href + '/');
                      const Icon = item.icon;
                      return (
                        <TransitionLink
                          key={item.href}
                          href={item.href}
                          onClick={() => handleNav(item.href)}
                          className={`flex items-center gap-2.5 px-2 h-8 rounded-[5px] cursor-pointer transition-colors duration-100
                            ${isActive ? 'bg-active text-text' : 'text-text-tertiary hover:bg-hover hover:text-text-secondary'}`}
                        >
                          <Icon className="w-4 h-4 shrink-0" />
                          <span className={`text-[13px] truncate ${isActive ? 'font-[590]' : 'font-[450]'}`}>
                            {item.label}
                          </span>
                        </TransitionLink>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>

            {/* Bottom utilities */}
            <div className="shrink-0 border-t border-border/40 px-2 py-2 space-y-px">
              {/* Status */}
              <div className="flex items-center gap-2.5 px-2 h-7">
                <div className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-green' : 'bg-text-quaternary'}`} />
                <span className="text-[11px] text-text-quaternary font-[510]">
                  {connected ? 'Connected' : 'Offline'}
                </span>
              </div>

              {/* Search */}
              <button
                type="button"
                onClick={() => { setSidebarOpen(false); setCommandPaletteOpen(true); }}
                className="flex items-center gap-2.5 w-full px-2 h-7 rounded-[4px] cursor-pointer text-text-tertiary hover:bg-hover hover:text-text-secondary transition-colors"
              >
                <Search className="w-3.5 h-3.5" />
                <span className="text-[12px] font-[510]">Search</span>
                <kbd className="text-[10px] text-text-quaternary bg-bg-tertiary px-1 py-0.5 rounded-[3px] ml-auto">{'\u2318'}K</kbd>
              </button>

              {/* Theme */}
              <button
                type="button"
                onClick={toggleTheme}
                className="flex items-center gap-2.5 w-full px-2 h-7 rounded-[4px] cursor-pointer text-text-tertiary hover:bg-hover hover:text-text-secondary transition-colors"
              >
                {theme === 'dark' ? <Sun className="w-3.5 h-3.5" /> : <Moon className="w-3.5 h-3.5" />}
                <span className="text-[12px] font-[510]">Theme</span>
              </button>

              {/* Settings */}
              <TransitionLink
                href="/settings"
                onClick={() => handleNav('/settings')}
                className="flex items-center gap-2.5 w-full px-2 h-7 rounded-[4px] cursor-pointer text-text-tertiary hover:bg-hover hover:text-text-secondary transition-colors"
              >
                <Settings className="w-3.5 h-3.5" />
                <span className="text-[12px] font-[510]">Settings</span>
              </TransitionLink>
            </div>
          </div>

          {/* Animations */}
          <style>{`
            @keyframes fade-in {
              from { opacity: 0; }
              to { opacity: 1; }
            }
            @keyframes fade-out {
              from { opacity: 1; }
              to { opacity: 0; }
            }
            @keyframes slide-in-left {
              from { transform: translateX(-100%); opacity: 0.8; }
              to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slide-out-left {
              from { transform: translateX(0); opacity: 1; }
              to { transform: translateX(-100%); opacity: 0; }
            }
          `}</style>
        </>
      )}
    </>
  );
}

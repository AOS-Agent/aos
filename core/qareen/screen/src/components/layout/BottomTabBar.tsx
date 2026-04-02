import { useLocation } from 'react-router-dom';
import TransitionLink from '@/components/primitives/TransitionLink';
import { Mic, CheckSquare, Library, Users, Activity } from 'lucide-react';

const TABS = [
  { label: 'Companion', href: '/', icon: Mic },
  { label: 'Tasks', href: '/tasks', icon: CheckSquare },
  { label: 'Vault', href: '/vault', icon: Library },
  { label: 'People', href: '/people', icon: Users },
  { label: 'System', href: '/system', icon: Activity },
] as const;

export default function BottomTabBar() {
  const { pathname } = useLocation();

  return (
    <nav
      className="md:hidden fixed inset-x-0 bottom-0 z-[90] flex items-center justify-around
        bg-bg-panel/90 backdrop-blur-xl border-t border-border"
      style={{
        height: 'calc(56px + env(safe-area-inset-bottom, 0px))',
        paddingBottom: 'env(safe-area-inset-bottom, 0px)',
      }}
    >
      {TABS.map((tab) => {
        const isActive = tab.href === '/'
          ? pathname === '/'
          : pathname === tab.href || pathname.startsWith(tab.href + '/');
        const Icon = tab.icon;
        return (
          <TransitionLink
            key={tab.href}
            href={tab.href}
            className={`flex flex-col items-center justify-center gap-0.5 flex-1 h-full pt-1.5
              ${isActive ? 'text-accent' : 'text-text-quaternary active:text-text-tertiary'}`}
          >
            <Icon className="w-5 h-5" />
            <span className={`text-[10px] ${isActive ? 'font-[590]' : 'font-[450]'}`}>
              {tab.label}
            </span>
          </TransitionLink>
        );
      })}
    </nav>
  );
}

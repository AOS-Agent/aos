import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import Topbar from './Topbar';
import MobileHeader from './MobileHeader';
import BottomTabBar from './BottomTabBar';
import CommandPalette from './CommandPalette';
import { useSSE } from '@/hooks/useSSE';
import { useNotifications } from '@/hooks/useNotifications';
import { useQmdWarmup } from '@/hooks/useQmdWarmup';

function SSEProvider({ children }: { children: React.ReactNode }) {
  useSSE();
  useNotifications();
  useQmdWarmup();
  return <>{children}</>;
}

export default function Layout() {
  return (
    <SSEProvider>
      <div className="flex flex-col h-dvh overflow-hidden">
        <MobileHeader />
        <div className="hidden md:block">
          <Topbar />
        </div>
        <div className="flex flex-1 min-h-0 overflow-hidden">
          <div className="hidden md:block">
            <Sidebar />
          </div>
          <main className="flex-1 min-w-0 overflow-y-auto px-4 sm:px-6 md:px-8 py-4 sm:py-6">
            <Outlet />
          </main>
        </div>
        <CommandPalette />
      </div>
      <BottomTabBar />
    </SSEProvider>
  );
}

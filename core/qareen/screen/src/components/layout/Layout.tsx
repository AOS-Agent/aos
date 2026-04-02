import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
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
      {/* Full-screen content — sidebar floats on top */}
      <div className="h-dvh overflow-hidden">
        <main className="h-full overflow-hidden">
          <Outlet />
        </main>
      </div>
      {/* Floating sidebar (pill + overlay drawer) */}
      <Sidebar />
      <CommandPalette />
      <BottomTabBar />
    </SSEProvider>
  );
}

import { Suspense, useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import CommandPalette from './CommandPalette';
import FloatingAgent from '../agent/FloatingAgent';
import { ToastContainer } from './NotificationToast';
import { useSSE } from '@/hooks/useSSE';
import { useNotifications } from '@/hooks/useNotifications';
import { useQmdWarmup } from '@/hooks/useQmdWarmup';
import { useToastQueue, useNotificationSSE } from '@/hooks/useNotificationStream';
import { usePrayerAmbient } from '@/hooks/usePrayerAmbient';
import { PageActionsProvider } from '@/hooks/usePageActions';
import { useQareenContext } from '@/store/qareenContext';

function SSEProvider({ children }: { children: React.ReactNode }) {
  useSSE();
  useNotifications();
  useQmdWarmup();
  return <>{children}</>;
}

/** Hydrate qareen context store on mount + track page visits */
function QareenContextHydrator() {
  const hydrate = useQareenContext(s => s.hydrate);
  const addPageVisit = useQareenContext(s => s.addPageVisit);
  const loaded = useQareenContext(s => s.loaded);
  const { pathname } = useLocation();

  useEffect(() => { if (!loaded) hydrate() }, [loaded, hydrate]);
  useEffect(() => { if (loaded) addPageVisit(pathname) }, [pathname, loaded, addPageVisit]);

  return null;
}

function NotificationLayer() {
  const { toasts, addToast, dismissToast } = useToastQueue();
  useNotificationSSE(addToast);
  return <ToastContainer toasts={toasts} onDismiss={dismissToast} />;
}

/** SVG noise filter — referenced by the grain overlay */
function GrainFilter() {
  return (
    <svg className="absolute w-0 h-0" aria-hidden="true">
      <filter id="grain">
        <feTurbulence type="fractalNoise" baseFrequency="0.65" numOctaves="3" stitchTiles="stitch" />
        <feColorMatrix type="saturate" values="0" />
      </filter>
    </svg>
  );
}

export default function Layout() {
  const { colors } = usePrayerAmbient();

  return (
    <SSEProvider>
      <PageActionsProvider>
        <QareenContextHydrator />
        <GrainFilter />

        {/* Film grain overlay — sits above everything, pointer-events: none */}
        <div
          className="fixed inset-0 pointer-events-none"
          style={{
            filter: 'url(#grain)',
            opacity: 0.030,
            mixBlendMode: 'overlay',
            zIndex: 9998,
          }}
        />

        {/* Full-screen content — gradient baked into background so pages can be transparent */}
        <div
          className="h-dvh overflow-hidden bg-bg"
          style={{
            backgroundImage: `radial-gradient(ellipse at 30% 20%, ${colors[0]} 0%, transparent 70%),
                              radial-gradient(ellipse at 70% 80%, ${colors[1]} 0%, transparent 70%)`,
          }}
        >
          <main className="h-full overflow-hidden">
            <Suspense>
              <Outlet />
            </Suspense>
          </main>
        </div>
        {/* Floating chrome */}
        <Sidebar />
        <FloatingAgent />
        <CommandPalette />
        <NotificationLayer />
      </PageActionsProvider>
    </SSEProvider>
  );
}

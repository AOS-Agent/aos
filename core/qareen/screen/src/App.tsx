import { lazy, Suspense } from 'react';
import { Routes, Route } from 'react-router-dom';
import Layout from '@/components/layout/Layout';
import { Skeleton } from '@/components/primitives';

const Companion = lazy(() => import('@/pages/Companion'));
const Home = lazy(() => import('@/pages/Home'));
const Tasks = lazy(() => import('@/pages/Tasks'));
const People = lazy(() => import('@/pages/People'));
const Vault = lazy(() => import('@/pages/Vault'));
const Agents = lazy(() => import('@/pages/Agents'));
const System = lazy(() => import('@/pages/System'));
const Config = lazy(() => import('@/pages/Config'));
const Analytics = lazy(() => import('@/pages/Analytics'));
const Channels = lazy(() => import('@/pages/Channels'));
const Pipelines = lazy(() => import('@/pages/Pipelines'));
const Projects = lazy(() => import('@/pages/Projects'));
const Calendar = lazy(() => import('@/pages/Calendar'));
const Chief = lazy(() => import('@/pages/Chief'));
const Meeting = lazy(() => import('@/pages/Meeting'));
const Approvals = lazy(() => import('@/pages/Approvals'));
const Memory = lazy(() => import('@/pages/Memory'));
const Sessions = lazy(() => import('@/pages/Sessions'));
const SessionDetail = lazy(() => import('@/pages/SessionDetail'));
const Settings = lazy(() => import('@/pages/Settings'));

function PageFallback() {
  return (
    <div className="space-y-4 py-2">
      <Skeleton className="h-7 w-48" />
      <Skeleton className="h-4 w-80" />
      <div className="mt-8 space-y-3">
        <Skeleton className="h-16 w-full rounded-[7px]" />
        <Skeleton className="h-16 w-full rounded-[7px]" />
        <Skeleton className="h-16 w-full rounded-[7px]" />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Companion />} />
          <Route path="/home" element={<Home />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/people" element={<People />} />
          <Route path="/docs" element={<Vault />} />
          <Route path="/vault" element={<Vault />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/system" element={<System />} />
          <Route path="/config" element={<Config />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/pipelines" element={<Pipelines />} />
          <Route path="/pipeline" element={<Pipelines />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/calendar" element={<Calendar />} />
          <Route path="/chief" element={<Chief />} />
          <Route path="/meeting" element={<Meeting />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/memory" element={<Memory />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:id" element={<SessionDetail />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

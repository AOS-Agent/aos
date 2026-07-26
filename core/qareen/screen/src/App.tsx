import { lazy, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import Layout from '@/components/layout/Layout';
import { migrateLegacyChatIfNeeded } from '@/lib/migrateLegacyChat';
import { PAGE_LOADERS } from '@/lib/routeLoaders';

// Route chunks come from PAGE_LOADERS so that TransitionLink prefetches the
// exact same module a route will render. Declaring the import inline here as
// well would let the two drift, and a prefetch that warms the wrong chunk is
// indistinguishable from no prefetch at all.

// ── Primary surfaces ──
const Home = lazy(PAGE_LOADERS['/']);
const CompanionSession = lazy(PAGE_LOADERS['/companion/session']);
const Work = lazy(PAGE_LOADERS['/work']);
const People = lazy(PAGE_LOADERS['/people']);
const Chat = lazy(PAGE_LOADERS['/chat']);
const System = lazy(PAGE_LOADERS['/system']);
const Settings = lazy(PAGE_LOADERS['/settings']);
const Days = lazy(PAGE_LOADERS['/timeline']);
const Agents = lazy(PAGE_LOADERS['/agents']);
const Org = lazy(PAGE_LOADERS['/org']);
const Skills = lazy(PAGE_LOADERS['/skills']);

// ── Sub-views ──
const Sessions = lazy(PAGE_LOADERS['/sessions']);
const SessionDetail = lazy(PAGE_LOADERS['/sessions/:id']);
const AgentConfig = lazy(PAGE_LOADERS['/agents/:id']);
const IntelligenceFeed = lazy(() => import('@/pages/IntelligenceFeed'));
const IntelligenceDetail = lazy(PAGE_LOADERS['/intelligence/:id']);
const IntelligenceSources = lazy(PAGE_LOADERS['/intelligence/sources']);
const Knowledge = lazy(PAGE_LOADERS['/knowledge']);
const Shipments = lazy(PAGE_LOADERS['/shipments']);
const ShipmentDetail = lazy(PAGE_LOADERS['/shipments/:id']);
const ShipmentsEval = lazy(PAGE_LOADERS['/shipments/eval']);

// ── Review: pages with real UI, kept for evaluation ──
const Calendar = lazy(PAGE_LOADERS['/calendar']);

export default function App() {
  // One-time migration: move legacy chat localStorage → SQLite conversations
  useEffect(() => { migrateLegacyChatIfNeeded() }, []);

  return (
    <Routes>
      <Route element={<Layout />}>
        {/* ── Primary surfaces ── */}
        <Route path="/" element={<Home />} />
        <Route path="/companion/session/:sessionId" element={<CompanionSession />} />
        <Route path="/work" element={<Work />} />
        <Route path="/people" element={<People />} />
        <Route path="/timeline" element={<Days />} />
        <Route path="/timeline/*" element={<Days />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/system" element={<System />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/:id" element={<AgentConfig />} />
        <Route path="/skills" element={<Skills />} />
        <Route path="/org" element={<Org />} />

        {/* ── Shipments — Auto Tracker dashboard ── */}
        <Route path="/shipments" element={<Shipments />} />
        <Route path="/shipments/eval" element={<ShipmentsEval />} />
        <Route path="/shipments/:id" element={<ShipmentDetail />} />

        {/* ── Knowledge — unified home for intelligence, library, topics, pipeline ── */}
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/knowledge/feed" element={<Knowledge />} />
        <Route path="/knowledge/library" element={<Knowledge />} />
        <Route path="/knowledge/topics" element={<Knowledge />} />
        <Route path="/knowledge/pipeline" element={<Knowledge />} />

        {/* ── Legacy intelligence routes — redirect to Knowledge ── */}
        <Route path="/intelligence" element={<Navigate to="/knowledge/feed" replace />} />
        <Route path="/intelligence/sources" element={<IntelligenceSources />} />
        <Route path="/intelligence/:id" element={<IntelligenceDetail />} />

        {/* ── Sub-routes ── */}
        <Route path="/sessions" element={<Sessions />} />
        <Route path="/sessions/:id" element={<SessionDetail />} />

        {/* ── Review: pages kept for evaluation ── */}
        {/* meeting route removed — companion sessions handle all session types */}
        <Route path="/calendar" element={<Calendar />} />

        {/* Vault/Knowledge, Intelligence, Integrations, Sentinel, Automations,
            and Approvals ship in later waves (knowledge pipeline, automations,
            comms/sentinel respectively) — a nav entry lands in the same PR as
            its route, never before it. */}

        {/* Catch-all: unknown / deep-linked / typo'd URLs redirect home instead of
            rendering a blank screen (e.g. /today, which has no route). */}
        <Route path="*" element={<Navigate to="/" replace />} />

      </Route>
    </Routes>
  );
}

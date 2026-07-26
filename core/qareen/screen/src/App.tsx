import { lazy, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import Layout from '@/components/layout/Layout';
import { migrateLegacyChatIfNeeded } from '@/lib/migrateLegacyChat';
import { routeComponent } from '@/lib/routeLoaders';

// Route chunks come from PAGE_LOADERS so that TransitionLink prefetches the
// exact same module a route will render. Declaring the import inline here as
// well would let the two drift, and a prefetch that warms the wrong chunk is
// indistinguishable from no prefetch at all.

// ── Primary surfaces ──
const Home = routeComponent('/');
const CompanionSession = routeComponent('/companion/session');
const Work = routeComponent('/work');
const People = routeComponent('/people');
const Chat = routeComponent('/chat');
const System = routeComponent('/system');
const Settings = routeComponent('/settings');
const Days = routeComponent('/timeline');
const Agents = routeComponent('/agents');
const Org = routeComponent('/org');
const Skills = routeComponent('/skills');

// ── Sub-views ──
const Sessions = routeComponent('/sessions');
const SessionDetail = routeComponent('/sessions/:id');
const AgentConfig = routeComponent('/agents/:id');
const IntelligenceFeed = lazy(() => import('@/pages/IntelligenceFeed'));
const IntelligenceDetail = routeComponent('/intelligence/:id');
const IntelligenceSources = routeComponent('/intelligence/sources');
const Knowledge = routeComponent('/knowledge');
const Shipments = routeComponent('/shipments');
const ShipmentDetail = routeComponent('/shipments/:id');
const ShipmentsEval = routeComponent('/shipments/eval');

// ── Review: pages with real UI, kept for evaluation ──
const Calendar = routeComponent('/calendar');

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

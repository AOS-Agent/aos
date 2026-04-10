import { lazy, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import Layout from '@/components/layout/Layout';
import { migrateLegacyChatIfNeeded } from '@/lib/migrateLegacyChat';

// ── Primary surfaces ──
const Home = lazy(() => import('@/pages/Companion'));
const CompanionSession = lazy(() => import('@/pages/CompanionSession'));
const Work = lazy(() => import('@/pages/Work'));
const People = lazy(() => import('@/pages/People'));
const Chat = lazy(() => import('@/pages/Chat'));
const System = lazy(() => import('@/pages/System'));
const Settings = lazy(() => import('@/pages/Settings'));
const Days = lazy(() => import('@/pages/Days'));
const Agents = lazy(() => import('@/pages/Agents'));
const Org = lazy(() => import('@/pages/Org'));
const Skills = lazy(() => import('@/pages/Skills'));

// ── Sub-views ──
const Sessions = lazy(() => import('@/pages/Sessions'));
const SessionDetail = lazy(() => import('@/pages/SessionDetail'));
const AgentConfig = lazy(() => import('@/pages/AgentConfig'));
const IntelligenceFeed = lazy(() => import('@/pages/IntelligenceFeed'));
const IntelligenceDetail = lazy(() => import('@/pages/IntelligenceDetail'));
const IntelligenceSources = lazy(() => import('@/pages/IntelligenceSources'));
const Knowledge = lazy(() => import('@/pages/Knowledge'));

// ── Review: pages with real UI, kept for evaluation ──
const Calendar = lazy(() => import('@/pages/Calendar'));

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

import { lazy, Suspense, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Skeleton } from '@/components/primitives/Skeleton';
import { KnowledgeStatusStrip } from '@/components/knowledge/KnowledgeStatusStrip';

// ---------------------------------------------------------------------------
// Knowledge — unified home for Intelligence, Vault, Topics, Pipeline.
//
// One top-level page, 5 tabs driven off the URL path so deep links work:
//
//   /knowledge          → Today
//   /knowledge/feed     → Feed (reworked IntelligenceFeed)
//   /knowledge/library  → Library (Part 7)
//   /knowledge/topics   → Topics (Part 7)
//   /knowledge/pipeline → Pipeline (Part 7)
//
// All 5 views share the status strip at the top.
// ---------------------------------------------------------------------------

const KnowledgeToday = lazy(() => import('@/components/knowledge/views/KnowledgeToday'));
const KnowledgeFeed = lazy(() => import('@/components/knowledge/views/KnowledgeFeed'));
const KnowledgeLibrary = lazy(() => import('@/components/knowledge/views/KnowledgeLibrary'));
const KnowledgeTopics = lazy(() => import('@/components/knowledge/views/KnowledgeTopics'));
const KnowledgePipeline = lazy(() => import('@/components/knowledge/views/KnowledgePipeline'));

type TabId = 'today' | 'feed' | 'library' | 'topics' | 'pipeline';

const TABS: { id: TabId; label: string; path: string }[] = [
  { id: 'today',    label: 'Today',    path: '/knowledge' },
  { id: 'feed',     label: 'Feed',     path: '/knowledge/feed' },
  { id: 'library',  label: 'Library',  path: '/knowledge/library' },
  { id: 'topics',   label: 'Topics',   path: '/knowledge/topics' },
  { id: 'pipeline', label: 'Pipeline', path: '/knowledge/pipeline' },
];

function tabFromPath(pathname: string): TabId {
  if (pathname.startsWith('/knowledge/feed')) return 'feed';
  if (pathname.startsWith('/knowledge/library')) return 'library';
  if (pathname.startsWith('/knowledge/topics')) return 'topics';
  if (pathname.startsWith('/knowledge/pipeline')) return 'pipeline';
  return 'today';
}

export default function Knowledge() {
  const navigate = useNavigate();
  const location = useLocation();
  const active = useMemo(() => tabFromPath(location.pathname), [location.pathname]);

  const View = useMemo(() => {
    switch (active) {
      case 'feed':     return KnowledgeFeed;
      case 'library':  return KnowledgeLibrary;
      case 'topics':   return KnowledgeTopics;
      case 'pipeline': return KnowledgePipeline;
      default:         return KnowledgeToday;
    }
  }, [active]);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Status strip — always visible across all Knowledge views */}
      <KnowledgeStatusStrip />

      {/* Tab bar — glass pill tabs per the design language */}
      <div className="shrink-0 px-6 pt-3 pb-2">
        <div className="flex items-center gap-1 p-1 bg-bg-secondary/50 border border-border rounded-full w-fit backdrop-blur-sm">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => navigate(tab.path)}
              className={`h-7 px-4 rounded-full text-[11px] font-[520] cursor-pointer transition-colors duration-75 ${
                active === tab.id
                  ? 'bg-bg-tertiary text-text'
                  : 'text-text-tertiary hover:text-text-secondary hover:bg-hover/60'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Active view */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <Suspense
          fallback={
            <div className="max-w-[820px] mx-auto px-6 pt-6">
              <Skeleton className="h-10 w-full mb-4" />
              <Skeleton className="h-40 w-full mb-3" />
              <Skeleton className="h-40 w-full" />
            </div>
          }
        >
          <View />
        </Suspense>
      </div>
    </div>
  );
}

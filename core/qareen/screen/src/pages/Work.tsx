import { useState, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useRegisterPageActions, type PageAction } from '@/hooks/usePageActions';
import TasksContent from '@/pages/Tasks';
import ProjectsContent from '@/pages/Projects';
import GoalsContent from '@/pages/Goals';
import TodayContent from '@/pages/Today';
import ProjectDetail from '@/pages/ProjectDetail';
import InitiativeDetail from '@/pages/InitiativeDetail';
import { TaskOverlayProvider } from '@/components/tasks/TaskOverlayContext';

type WorkTab = 'today' | 'tasks' | 'projects' | 'goals';
const TABS: WorkTab[] = ['today', 'tasks', 'projects', 'goals'];

interface ViewSnapshot { tab: WorkTab; projectId: string | null; initiativeId: string | null; }

export default function WorkPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get('tab') as WorkTab | null;
  const [activeTab, setActiveTab] = useState<WorkTab>(
    tabParam && TABS.includes(tabParam) ? tabParam : 'today'
  );
  // Retained only for the Tasks filter deep-link.
  const [projectFilter] = useState<string | null>(searchParams.get('project'));
  const [openProjectId, setOpenProjectId] = useState<string | null>(null);
  const [openInitiativeId, setOpenInitiativeId] = useState<string | null>(null);
  // Navigation history across tab switches AND detail drill-ins (project or
  // initiative), so Back always returns to the previous view from anywhere.
  const [history, setHistory] = useState<ViewSnapshot[]>([]);

  const snapshot = useCallback(
    (): ViewSnapshot => ({ tab: activeTab, projectId: openProjectId, initiativeId: openInitiativeId }),
    [activeTab, openProjectId, openInitiativeId]
  );

  const handleTabChange = useCallback((tab: WorkTab) => {
    if (tab === activeTab && !openProjectId && !openInitiativeId) return;
    setHistory(h => [...h, snapshot()]);
    setActiveTab(tab);
    setOpenProjectId(null);
    setOpenInitiativeId(null);
    setSearchParams({ tab }, { replace: true });
  }, [activeTab, openProjectId, openInitiativeId, snapshot, setSearchParams]);

  // Drill into a project — opens the full-canvas detail, recording where we were.
  const openProject = useCallback((projectId: string) => {
    setHistory(h => [...h, snapshot()]);
    setOpenProjectId(projectId);
    setOpenInitiativeId(null);
  }, [snapshot]);

  // Drill into an initiative — same history model as openProject.
  const openInitiative = useCallback((slug: string) => {
    setHistory(h => [...h, snapshot()]);
    setOpenInitiativeId(slug);
    setOpenProjectId(null);
  }, [snapshot]);

  // Universal back — pops the last view (tab, project, or initiative) and restores it.
  const goBack = useCallback(() => {
    setHistory(h => {
      if (h.length === 0) return h;
      const prev = h[h.length - 1];
      setActiveTab(prev.tab);
      setOpenProjectId(prev.projectId);
      setOpenInitiativeId(prev.initiativeId);
      return h.slice(0, -1);
    });
  }, []);

  const pageActions: PageAction[] = useMemo(() => [
    {
      id: 'work.switch_tab',
      label: 'Switch Work tab',
      category: 'navigate',
      params: [{ name: 'tab', type: 'enum' as const, required: true, description: 'Tab to switch to', options: TABS }],
      execute: ({ tab }) => handleTabChange(tab as WorkTab),
    },
  ], [handleTabChange]);
  useRegisterPageActions(pageActions);

  const canBack = history.length > 0;
  const detailOpen = openProjectId || openInitiativeId;

  return (
    <TaskOverlayProvider>
    <div className="h-full flex flex-col">
      {/* Tab pills — centered, glass */}
      <div className="shrink-0 flex justify-center pt-3 pb-2 pointer-events-none">
        <div
          className="flex items-center gap-1 h-9 px-1 rounded-full border pointer-events-auto"
          style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)', borderColor: 'var(--glass-border)', boxShadow: 'var(--glass-shadow)' }}
        >
          {canBack && !detailOpen && (
            <>
              <button
                onClick={goBack}
                aria-label="Back"
                className="px-2 h-7 rounded-full flex items-center text-text-tertiary hover:text-text cursor-pointer transition-colors duration-150"
              >
                <ArrowLeft className="w-4 h-4" />
              </button>
              <div className="w-px h-4 mx-0.5 shrink-0" style={{ background: 'var(--glass-border)' }} />
            </>
          )}
          {TABS.map(tab => (
            <button
              key={tab}
              onClick={() => handleTabChange(tab)}
              className={`px-3.5 h-7 rounded-full text-[14px] font-[510] cursor-pointer transition-all duration-150 ${
                activeTab === tab ? 'bg-[rgba(255,245,235,0.10)] text-text' : 'text-text-tertiary hover:text-text-secondary'
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 relative">
        {/* list layer — kept MOUNTED (hidden) so collapse/scroll survive the round-trip */}
        <div className={detailOpen ? 'hidden' : 'h-full'}>
          {activeTab === 'today' && <TodayContent onProjectClick={openProject} />}
          {activeTab === 'tasks' && <TasksContent initialProjectFilter={projectFilter} />}
          {activeTab === 'projects' && <ProjectsContent onProjectClick={openProject} />}
          {activeTab === 'goals' && <GoalsContent onProjectClick={openProject} onOpenInitiative={openInitiative} />}
        </div>

        {/* detail layer — project and initiative are mutually exclusive */}
        {openProjectId && (
          <ProjectDetail projectId={openProjectId} backLabel={activeTab} onBack={goBack} />
        )}
        {openInitiativeId && (
          <InitiativeDetail slug={openInitiativeId} onBack={goBack} onOpenProject={openProject} />
        )}
      </div>
    </div>
    </TaskOverlayProvider>
  );
}

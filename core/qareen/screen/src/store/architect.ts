/**
 * Architect workspace store — single source of truth for the flow spec.
 *
 * The session hook (SSE transport) writes here via setSpec().
 * Components read spec, UI state, and mutations from here only.
 */
import { create } from 'zustand';
import type { FlowSystemSpec } from '@/lib/architect-types';

export type WorkspaceTab = 'flow' | 'trace' | 'waterfall' | 'output';

export interface ArchitectState {
  // Spec — sole owner
  spec: FlowSystemSpec | null;
  dirty: boolean;

  // Workspace UI
  activeTab: WorkspaceTab;
  expandedStepId: string | null;

  // Spec setters
  setSpec: (spec: FlowSystemSpec | null) => void;
  clearSpec: () => void;

  // UI actions
  setActiveTab: (tab: WorkspaceTab) => void;
  setExpandedStep: (id: string | null) => void;
  toggleExpandedStep: (id: string) => void;

  // Spec mutations (mark dirty)
  updateStepParam: (pipelineId: string, stepId: string, key: string, value: unknown) => void;
  reorderStep: (pipelineId: string, fromIndex: number, toIndex: number) => void;
  removeStep: (pipelineId: string, stepId: string) => void;
  addStep: (pipelineId: string, afterIndex: number, step: any) => void;
}

export const useArchitectStore = create<ArchitectState>((set) => ({
  spec: null,
  dirty: false,
  activeTab: 'flow',
  expandedStepId: null,

  setSpec: (spec) => set({ spec, dirty: false }),
  clearSpec: () => set({ spec: null, dirty: false, expandedStepId: null }),

  setActiveTab: (tab) => set({ activeTab: tab }),
  setExpandedStep: (id) => set({ expandedStepId: id }),
  toggleExpandedStep: (id) =>
    set((s) => ({ expandedStepId: s.expandedStepId === id ? null : id })),

  updateStepParam: (pipelineId, stepId, key, value) =>
    set((s) => {
      if (!s.spec) return s;
      const pipelines = s.spec.pipelines.map((p) => {
        if (p.id !== pipelineId) return p;
        return {
          ...p,
          steps: p.steps.map((step) => {
            if (step.id !== stepId) return step;
            return { ...step, parameters: { ...step.parameters, [key]: value } };
          }),
        };
      });
      return { spec: { ...s.spec, pipelines }, dirty: true };
    }),

  reorderStep: (pipelineId, fromIndex, toIndex) =>
    set((s) => {
      if (!s.spec) return s;
      const pipelines = s.spec.pipelines.map((p) => {
        if (p.id !== pipelineId) return p;
        const steps = [...p.steps];
        const [moved] = steps.splice(fromIndex, 1);
        steps.splice(toIndex, 0, moved);
        return { ...p, steps };
      });
      return { spec: { ...s.spec, pipelines }, dirty: true };
    }),

  removeStep: (pipelineId, stepId) =>
    set((s) => {
      if (!s.spec) return s;
      const pipelines = s.spec.pipelines.map((p) => {
        if (p.id !== pipelineId) return p;
        return { ...p, steps: p.steps.filter((step) => step.id !== stepId) };
      });
      return { spec: { ...s.spec, pipelines }, dirty: true };
    }),

  addStep: (pipelineId, afterIndex, step) =>
    set((s) => {
      if (!s.spec) return s;
      const pipelines = s.spec.pipelines.map((p) => {
        if (p.id !== pipelineId) return p;
        const steps = [...p.steps];
        steps.splice(afterIndex + 1, 0, step);
        return { ...p, steps };
      });
      return { spec: { ...s.spec, pipelines }, dirty: true };
    }),
}));

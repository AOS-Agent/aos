/**
 * Shared types for the Automation Architect feature.
 * Lives here to avoid circular deps between store/architect and hooks/useArchitectSession.
 */

export interface ArchitectMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
}

export type ArchitectPhase =
  | 'investigate'
  | 'decompose'
  | 'design'
  | 'enhance'
  | 'confirm';

export interface FlowSystemSpec {
  name: string;
  objective: string;
  pipelines: PipelineSpec[];
  enhancements?: Enhancement[];
}

export interface PipelineSpec {
  id: string;
  name: string;
  complexity: 'simple' | 'complex' | 'super-complex';
  trigger: { type: string; parameters: Record<string, unknown> };
  steps: StepSpec[];
  calls_pipelines?: string[];
}

export interface StepSpec {
  id: string;
  type: 'n8n_node' | 'agent_dispatch' | 'hitl_approval' | 'sub_workflow';
  n8n_type?: string;
  agent_id?: string;
  label: string;
  parameters: Record<string, unknown>;
  next?: string[];
  branch_conditions?: { condition: string; expression: string; target_step: string }[];
}

export interface Enhancement {
  title: string;
  description: string;
  complexity: 'simple' | 'complex';
}

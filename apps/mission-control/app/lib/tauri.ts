import { invoke } from "@tauri-apps/api/core";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface OperatorConfig {
  name: string;
  timezone: string;
  locale: string;
  theme: "light" | "dark" | "system";
  notifications: boolean;
  services: Record<string, boolean>;
}

export interface AgentMeta {
  id: string;
  name: string;
  role: string;
  source: "system" | "catalog" | "custom";
  active: boolean;
  description: string;
}

export interface ServiceStatus {
  name: string;
  status: "running" | "stopped" | "error" | "starting";
  port?: number;
  pid?: number;
  uptime?: number;
  lastError?: string;
}

export interface SearchResult {
  path: string;
  title: string;
  snippet: string;
  score: number;
  collection: string;
}

export interface OnboardingStatus {
  completed: boolean;
  timestamp?: string;
}

// ---------------------------------------------------------------------------
// Tauri Command Wrappers
// ---------------------------------------------------------------------------

/**
 * Read the operator configuration from ~/.aos/config/operator.yaml
 */
export async function readOperatorConfig(): Promise<OperatorConfig> {
  return invoke<OperatorConfig>("read_operator_config");
}

/**
 * Check whether onboarding has been completed.
 */
export async function checkOnboardingStatus(): Promise<OnboardingStatus> {
  return invoke<OnboardingStatus>("check_onboarding_status");
}

/**
 * List all agents (system, catalog, and custom).
 */
export async function listAgents(): Promise<AgentMeta[]> {
  return invoke<AgentMeta[]>("list_agents");
}

/**
 * Get the current status of all registered services.
 */
export async function getServiceStatus(): Promise<ServiceStatus[]> {
  return invoke<ServiceStatus[]>("get_service_status");
}

/**
 * Search the knowledge vault using QMD.
 */
export async function searchVault(
  query: string,
  collection?: string
): Promise<SearchResult[]> {
  return invoke<SearchResult[]>("search_vault", { query, collection });
}

/**
 * Read a file from the vault by relative path.
 */
export async function readVaultFile(path: string): Promise<string> {
  return invoke<string>("read_vault_file", { path });
}

/**
 * Run a work CLI command and return stdout.
 */
export async function runWorkCommand(args: string[]): Promise<string> {
  return invoke<string>("run_work_command", { args });
}

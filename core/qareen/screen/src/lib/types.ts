// =============================================================================
// Qareen API — TypeScript Type Definitions
// Generated from Pydantic schemas and ontology enums
// =============================================================================

// -----------------------------------------------------------------------------
// Ontology Enums
// -----------------------------------------------------------------------------

export enum ObjectType {
  PERSON = 'person',
  TASK = 'task',
  PROJECT = 'project',
  GOAL = 'goal',
  MESSAGE = 'message',
  NOTE = 'note',
  DECISION = 'decision',
  SESSION = 'session',
  AGENT = 'agent',
  CHANNEL = 'channel',
  INTEGRATION = 'integration',
  AREA = 'area',
  WORKFLOW = 'workflow',
  WORKFLOW_RUN = 'workflow_run',
  PIPELINE_ENTRY = 'pipeline_entry',
  REMINDER = 'reminder',
  TRANSACTION = 'transaction',
  PROCEDURE = 'procedure',
  CONVERSATION = 'conversation',
}

export enum TaskStatus {
  TODO = 'todo',
  ACTIVE = 'active',
  WAITING = 'waiting',
  DONE = 'done',
}

export enum TaskPriority {
  CRITICAL = 1,
  HIGH = 2,
  NORMAL = 3,
  LOW = 4,
  SOMEDAY = 5,
}

export enum ChannelType {
  TELEGRAM = 'telegram',
  WHATSAPP = 'whatsapp',
  EMAIL = 'email',
  SLACK = 'slack',
  SMS = 'sms',
}

export enum TrustLevel {
  OBSERVE = 0,
  SURFACE = 1,
  DRAFT = 2,
  ACT_WITH_DIGEST = 3,
  ACT_WITH_AUDIT = 4,
  AUTONOMOUS = 5,
}

export enum PipelineStage {
  QUEUED = 'queued',
  PROCESSING = 'processing',
  COMPLETED = 'completed',
  FAILED = 'failed',
  ESCALATED = 'escalated',
}

export enum NoteStage {
  CAPTURE = 1,
  TRIAGE = 2,
  RESEARCH = 3,
  SYNTHESIS = 4,
  DECISION = 5,
  EXPERTISE = 6,
}

export enum SessionStatus {
  ACTIVE = 'active',
  PAUSED = 'paused',
  ENDED = 'ended',
}

export enum MessageDirection {
  INBOUND = 'inbound',
  OUTBOUND = 'outbound',
}

// -----------------------------------------------------------------------------
// General Response Types
// -----------------------------------------------------------------------------

export interface ErrorResponse {
  error: string
  detail?: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface VersionResponse {
  version: string
  build?: string
  commit?: string
}

// -----------------------------------------------------------------------------
// Work — Tasks
// -----------------------------------------------------------------------------

export interface TaskHandoffSchema {
  state: string
  next: string
  files?: string[]
  decisions?: string[]
  blockers?: string[]
  session_id?: string
  timestamp?: string
}

export interface TaskResponse {
  id: string
  title: string
  status: TaskStatus
  priority: TaskPriority
  project?: string
  tags?: string[]
  due?: string
  created_at: string
  updated_at?: string
  completed_at?: string
  assignee?: string
  parent_id?: string
  subtasks?: TaskResponse[]
  handoff?: TaskHandoffSchema
  notes?: string
  links?: string[]
}

export interface TaskListResponse {
  tasks: TaskResponse[]
  total: number
}

export interface CreateTaskRequest {
  title: string
  priority?: TaskPriority
  project?: string
  tags?: string[]
  due?: string
  assignee?: string
  parent_id?: string
  notes?: string
}

export interface UpdateTaskRequest {
  title?: string
  status?: TaskStatus
  priority?: TaskPriority
  project?: string
  tags?: string[]
  due?: string
  assignee?: string
  notes?: string
}

export interface WriteHandoffRequest {
  state: string
  next: string
  files?: string[]
  decisions?: string[]
  blockers?: string[]
  session_id?: string
}

// -----------------------------------------------------------------------------
// Work — Projects
// -----------------------------------------------------------------------------

export interface ProjectResponse {
  id: string
  name: string
  description?: string
  status: string
  task_count: number
  active_task_count: number
  created_at: string
  updated_at?: string
}

export interface ProjectListResponse {
  projects: ProjectResponse[]
  total: number
}

export interface CreateProjectRequest {
  name: string
  description?: string
}

// -----------------------------------------------------------------------------
// Work — Goals
// -----------------------------------------------------------------------------

export interface KeyResultSchema {
  id: string
  title: string
  target: number
  current: number
  unit?: string
}

export interface GoalResponse {
  id: string
  title: string
  description?: string
  status: string
  key_results: KeyResultSchema[]
  project?: string
  due?: string
  created_at: string
  updated_at?: string
}

export interface GoalListResponse {
  goals: GoalResponse[]
  total: number
}

export interface CreateGoalRequest {
  title: string
  description?: string
  key_results?: KeyResultSchema[]
  project?: string
  due?: string
}

// -----------------------------------------------------------------------------
// Work — Inbox
// -----------------------------------------------------------------------------

export interface InboxItemResponse {
  id: string
  text: string
  source?: string
  created_at: string
  triaged: boolean
  triaged_to?: string
}

export interface CreateInboxRequest {
  text: string
  source?: string
}

// -----------------------------------------------------------------------------
// Work — Combined
// -----------------------------------------------------------------------------

export interface WorkResponse {
  tasks: TaskResponse[]
  projects: ProjectResponse[]
  goals: GoalResponse[]
  inbox: InboxItemResponse[]
}

// -----------------------------------------------------------------------------
// Config
// -----------------------------------------------------------------------------

export interface OperatorResponse {
  name: string
  handle?: string
  email?: string
  timezone?: string
  locale?: string
  preferences: Record<string, any>
}

export interface UpdateOperatorRequest {
  name?: string
  handle?: string
  email?: string
  timezone?: string
  locale?: string
  preferences?: Record<string, any>
}

export interface AccountsResponse {
  accounts: Record<string, any>
}

export interface IntegrationSummary {
  name: string
  type: string
  status: string
  last_sync?: string
}

export interface IntegrationsResponse {
  integrations: IntegrationSummary[]
}

// -----------------------------------------------------------------------------
// Agents
// -----------------------------------------------------------------------------

export interface AgentResponse {
  name: string
  role: string
  status: string
  trust_level: TrustLevel
  description?: string
  skills?: string[]
  active: boolean
}

export interface AgentListResponse {
  agents: AgentResponse[]
}

export interface AgentCatalogResponse {
  catalog: AgentResponse[]
  active: string[]
}

export interface UpdateTrustRequest {
  trust_level: TrustLevel
}

// -----------------------------------------------------------------------------
// Skills
// -----------------------------------------------------------------------------

export interface SkillResponse {
  name: string
  description?: string
  triggers?: string[]
  enabled: boolean
  path?: string
}

export interface SkillListResponse {
  skills: SkillResponse[]
}

export interface ToggleSkillRequest {
  enabled: boolean
}

// -----------------------------------------------------------------------------
// Services
// -----------------------------------------------------------------------------

export interface ServiceResponse {
  name: string
  status: string
  pid?: number
  port?: number
  uptime?: string
  memory_mb?: number
  cpu_percent?: number
}

export interface ServiceListResponse {
  services: ServiceResponse[]
}

export interface ServiceLogsResponse {
  service: string
  lines: string[]
  total_lines: number
}

// -----------------------------------------------------------------------------
// Crons
// -----------------------------------------------------------------------------

export interface CronJobResponse {
  name: string
  schedule: string
  command: string
  enabled: boolean
  last_run?: string
  next_run?: string
  last_status?: string
}

export interface CronListResponse {
  crons: CronJobResponse[]
}

// -----------------------------------------------------------------------------
// People
// -----------------------------------------------------------------------------

export interface PersonResponse {
  id: string
  name: string
  aliases?: string[]
  channels?: Record<string, string>
  tags?: string[]
  notes?: string
  last_interaction?: string
  created_at: string
  updated_at?: string
}

export interface PersonListResponse {
  people: PersonResponse[]
  total: number
}

export interface InteractionSchema {
  id: string
  person_id: string
  channel: string
  direction: MessageDirection
  summary?: string
  timestamp: string
  message_id?: string
}

export interface RelationshipSchema {
  person_id: string
  related_person_id: string
  type: string
  strength?: number
  notes?: string
}

export interface PersonDetailResponse extends PersonResponse {
  interactions: InteractionSchema[]
  relationships: RelationshipSchema[]
  task_mentions: string[]
  vault_mentions: string[]
}

export interface UpdatePersonRequest {
  name?: string
  aliases?: string[]
  channels?: Record<string, string>
  tags?: string[]
  notes?: string
}

export interface PersonSearchRequest {
  query: string
  tags?: string[]
  limit?: number
}

export interface PersonSurfaceItem {
  person: PersonResponse
  reason: string
  score: number
}

export interface PersonSurfaceResponse {
  items: PersonSurfaceItem[]
}

// -----------------------------------------------------------------------------
// Vault
// -----------------------------------------------------------------------------

export interface VaultCollectionResponse {
  name: string
  path: string
  doc_count: number
  last_indexed?: string
}

export interface VaultCollectionListResponse {
  collections: VaultCollectionResponse[]
}

export interface VaultFileResponse {
  path: string
  title?: string
  content: string
  frontmatter: Record<string, any>
  collection: string
}

export interface VaultSearchResult {
  path: string
  title?: string
  snippet: string
  score: number
  collection: string
}

export interface VaultSearchResponse {
  results: VaultSearchResult[]
  total: number
  query: string
}

export interface VaultSearchRequest {
  query: string
  collection?: string
  limit?: number
  min_score?: number
}

// -----------------------------------------------------------------------------
// Secrets
// -----------------------------------------------------------------------------

export interface SecretEntry {
  name: string
  service: string
  created_at?: string
  updated_at?: string
}

export interface SecretListResponse {
  secrets: SecretEntry[]
}

export interface AddSecretRequest {
  name: string
  value: string
  service?: string
}

export interface RotateSecretRequest {
  name: string
  new_value: string
}

// -----------------------------------------------------------------------------
// System
// -----------------------------------------------------------------------------

export interface HealthResponse {
  status: string
  uptime?: string
  version?: string
  services: Record<string, string>
  checks: Record<string, boolean>
}

export interface StorageDevice {
  name: string
  mount_point: string
  total_gb: number
  used_gb: number
  free_gb: number
  percent_used: number
}

export interface SymlinkResponse {
  source: string
  target: string
  valid: boolean
}

export interface StorageResponse {
  devices: StorageDevice[]
  symlinks: SymlinkResponse[]
}

export interface ReconcileCheckResult {
  name: string
  passed: boolean
  message: string
  auto_fixable: boolean
}

export interface ReconcileResponse {
  checks: ReconcileCheckResult[]
  all_passed: boolean
  auto_fixed: number
}

// -----------------------------------------------------------------------------
// Channels
// -----------------------------------------------------------------------------

export interface ChannelStatusResponse {
  channel: ChannelType
  status: string
  connected: boolean
  last_message?: string
  error?: string
}

export interface ChannelListResponse {
  channels: ChannelStatusResponse[]
}

// -----------------------------------------------------------------------------
// Metrics
// -----------------------------------------------------------------------------

export interface MetricDataPoint {
  timestamp: string
  value: number
  labels?: Record<string, string>
}

export interface MetricResponse {
  name: string
  description?: string
  data_points: MetricDataPoint[]
  unit?: string
}

export interface MetricListResponse {
  metrics: MetricResponse[]
}

// -----------------------------------------------------------------------------
// Pipelines
// -----------------------------------------------------------------------------

export interface PipelineStageSchema {
  name: string
  status: PipelineStage
  started_at?: string
  completed_at?: string
  error?: string
  output?: Record<string, any>
}

export interface PipelineDefinitionSchema {
  id: string
  name: string
  description?: string
  stages: string[]
  created_at: string
}

export interface PipelineListResponse {
  pipelines: PipelineDefinitionSchema[]
}

export interface PipelineRunResponse {
  id: string
  pipeline_id: string
  status: PipelineStage
  stages: PipelineStageSchema[]
  started_at: string
  completed_at?: string
  input: Record<string, any>
  output?: Record<string, any>
  error?: string
}

export interface PipelineRunListResponse {
  runs: PipelineRunResponse[]
  total: number
}

// -----------------------------------------------------------------------------
// Companion Screen — Cards
// -----------------------------------------------------------------------------

export enum CardType {
  TASK = 'task',
  DECISION = 'decision',
  VAULT = 'vault',
  REPLY = 'reply',
  SYSTEM = 'system',
  SUGGESTION = 'suggestion',
}

export interface Card {
  id: string
  card_type: CardType
  title: string
  body: string
  status: 'pending' | 'approved_pending' | 'approved' | 'dismissed' | 'expired'
  created_at: string
  expires_at?: string
  source_utterance?: string
  confidence: number
}

export interface TaskCard extends Card {
  task_title: string
  task_project?: string
  task_priority: number
  task_assignee?: string
  task_due?: string
  is_update: boolean
  existing_task_id?: string
}

export interface DecisionCard extends Card {
  rationale: string
  stakeholders: string[]
  project?: string
  supersedes?: string
}

export interface VaultCard extends Card {
  note_type: string
  tags: string[]
  project?: string
  suggested_path?: string
}

export interface ReplyCard extends Card {
  channel: string
  recipient: string
  draft_text: string
  thread_id?: string
  original_message_id?: string
}

export interface SystemCard extends Card {
  severity: 'info' | 'warning' | 'error' | 'critical'
  service_name: string
  suggested_action?: string
  auto_resolve: boolean
}

export interface SuggestionCard extends Card {
  pattern: string
  observation: string
  suggested_actions: string[]
  related_entities: string[]
}

// -----------------------------------------------------------------------------
// SSE Events
// -----------------------------------------------------------------------------

export interface SSEEvent {
  event_type: string
  timestamp: string
  source: string
  payload: Record<string, any>
}

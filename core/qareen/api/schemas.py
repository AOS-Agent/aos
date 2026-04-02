"""Qareen API Schemas — Pydantic v2 models for all request/response shapes.

Every API endpoint uses typed schemas for validation and serialization.
Import ontology enums for field types where applicable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from ..ontology.types import (
    ChannelType,
    PipelineStage,
    TaskPriority,
    TaskStatus,
    TrustLevel,
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standard error envelope returned by all endpoints on failure."""

    error: str = Field(..., description="Human-readable error message")
    code: str = Field("internal_error", description="Machine-readable error code")
    detail: dict[str, Any] | None = Field(None, description="Extra context for debugging")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated wrapper for list endpoints."""

    items: list[T] = Field(default_factory=list, description="Page of results")
    total: int = Field(0, description="Total item count across all pages")
    page: int = Field(1, description="Current page number (1-indexed)")
    per_page: int = Field(50, description="Items per page")
    has_more: bool = Field(False, description="Whether more pages exist")


class VersionResponse(BaseModel):
    """AOS version information."""

    version: str = Field(..., description="Semantic version string", examples=["0.7.2"])
    codename: str | None = Field(None, description="Release codename", examples=["qareen"])
    build_date: str | None = Field(None, description="ISO date of build")
    python_version: str | None = Field(None, description="Python runtime version")


# ---------------------------------------------------------------------------
# Work — Tasks
# ---------------------------------------------------------------------------


class TaskHandoffSchema(BaseModel):
    """Handoff context attached to a task for agent continuity."""

    state: str = Field(..., description="What was accomplished and where things stand")
    next_step: str = Field(..., description="The specific next action to take")
    files: list[str] = Field(default_factory=list, description="Relevant file paths")
    decisions: list[str] = Field(default_factory=list, description="Decisions made")
    blockers: list[str] = Field(default_factory=list, description="Current blockers")
    session_id: str | None = Field(None, description="Session that wrote the handoff")
    timestamp: datetime | None = Field(None, description="When the handoff was written")


class TaskResponse(BaseModel):
    """A single task as returned by the API."""

    id: str = Field(..., description="Project-scoped task ID", examples=["aos#42"])
    title: str = Field(..., description="Task title")
    status: TaskStatus = Field(TaskStatus.TODO, description="Current task status")
    priority: TaskPriority = Field(TaskPriority.NORMAL, description="Priority level 1-5")
    project: str | None = Field(None, description="Project this task belongs to")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    description: str | None = Field(None, description="Longer task description")

    assigned_to: str | None = Field(None, description="Agent or person assigned")
    created_by: str | None = Field(None, description="Who created the task")

    created: datetime | None = Field(None, description="Creation timestamp")
    started: datetime | None = Field(None, description="When work began")
    completed: datetime | None = Field(None, description="Completion timestamp")
    due: datetime | None = Field(None, description="Due date")

    parent_id: str | None = Field(None, description="Parent task ID if subtask")
    subtask_ids: list[str] = Field(default_factory=list, description="Child subtask IDs")

    handoff: TaskHandoffSchema | None = Field(None, description="Handoff context")

    pipeline: str | None = Field(None, description="Pipeline processing this task")
    pipeline_stage: PipelineStage | None = Field(None, description="Current pipeline stage")
    recurrence: str | None = Field(None, description="Cron expression for recurring tasks")


class TaskListResponse(BaseModel):
    """List of tasks with summary counts."""

    tasks: list[TaskResponse] = Field(default_factory=list, description="All tasks")
    total: int = Field(0, description="Total task count")
    by_status: dict[str, int] = Field(default_factory=dict, description="Count per status")
    by_project: dict[str, int] = Field(default_factory=dict, description="Count per project")


class CreateTaskRequest(BaseModel):
    """Request body for creating a new task."""

    title: str = Field(..., description="Task title", min_length=1)
    priority: TaskPriority = Field(TaskPriority.NORMAL, description="Priority 1-5")
    project: str | None = Field(None, description="Project to assign to")
    tags: list[str] = Field(default_factory=list, description="Tags")
    description: str | None = Field(None, description="Longer description")
    assigned_to: str | None = Field(None, description="Assign to agent or person")
    due: datetime | None = Field(None, description="Due date")
    parent_id: str | None = Field(None, description="Parent task ID for subtasks")


class UpdateTaskRequest(BaseModel):
    """Request body for updating task fields. All fields optional."""

    title: str | None = Field(None, description="New title")
    status: TaskStatus | None = Field(None, description="New status")
    priority: TaskPriority | None = Field(None, description="New priority")
    project: str | None = Field(None, description="Move to project")
    tags: list[str] | None = Field(None, description="Replace tags")
    description: str | None = Field(None, description="New description")
    assigned_to: str | None = Field(None, description="Reassign")
    due: datetime | None = Field(None, description="New due date")


class WriteHandoffRequest(BaseModel):
    """Request body for writing a task handoff."""

    state: str = Field(..., description="What was accomplished")
    next_step: str = Field(..., description="Next action to take")
    files: list[str] = Field(default_factory=list, description="Relevant files")
    decisions: list[str] = Field(default_factory=list, description="Decisions made")
    blockers: list[str] = Field(default_factory=list, description="Blockers")
    session_id: str | None = Field(None, description="Current session ID")


# ---------------------------------------------------------------------------
# Work — Projects
# ---------------------------------------------------------------------------


class ProjectResponse(BaseModel):
    """A project as returned by the API."""

    id: str = Field(..., description="Project identifier", examples=["aos"])
    title: str = Field(..., description="Project title")
    description: str | None = Field(None, description="Project description")
    status: str = Field("active", description="Project status: active, paused, completed, archived")
    path: str | None = Field(None, description="Filesystem path")
    goal: str | None = Field(None, description="Project goal statement")
    done_when: str | None = Field(None, description="Completion criteria")

    stages: list[str] | None = Field(None, description="Pipeline stages")
    current_stage: str | None = Field(None, description="Current stage")

    task_count: int = Field(0, description="Total tasks in project")
    done_count: int = Field(0, description="Completed tasks")
    active_count: int = Field(0, description="Active tasks")


class ProjectListResponse(BaseModel):
    """List of all projects."""

    projects: list[ProjectResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total project count")


class CreateProjectRequest(BaseModel):
    """Request body for creating a project."""

    id: str = Field(..., description="Project identifier (short, lowercase)", min_length=1)
    title: str = Field(..., description="Project title", min_length=1)
    description: str | None = Field(None, description="Description")
    path: str | None = Field(None, description="Filesystem path")
    goal: str | None = Field(None, description="Goal statement")
    done_when: str | None = Field(None, description="Completion criteria")


# ---------------------------------------------------------------------------
# Work — Goals
# ---------------------------------------------------------------------------


class KeyResultSchema(BaseModel):
    """A key result within a goal."""

    title: str = Field(..., description="Key result title")
    progress: int = Field(0, description="Progress percentage 0-100", ge=0, le=100)
    target: str | None = Field(None, description="Target metric description")


class GoalResponse(BaseModel):
    """A goal as returned by the API."""

    id: str = Field(..., description="Goal identifier")
    title: str = Field(..., description="Goal title")
    weight: int = Field(0, description="Weight percentage (all goals sum to 100)")
    description: str | None = Field(None, description="Goal description")
    key_results: list[KeyResultSchema] = Field(default_factory=list, description="Key results")
    project: str | None = Field(None, description="Associated project")


class GoalListResponse(BaseModel):
    """List of all goals."""

    goals: list[GoalResponse] = Field(default_factory=list)
    total_weight: int = Field(0, description="Sum of all goal weights (should be 100)")


class CreateGoalRequest(BaseModel):
    """Request body for creating a goal."""

    title: str = Field(..., description="Goal title", min_length=1)
    weight: int = Field(0, description="Weight percentage", ge=0, le=100)
    description: str | None = Field(None, description="Description")
    key_results: list[KeyResultSchema] = Field(default_factory=list, description="Key results")
    project: str | None = Field(None, description="Associated project")


# ---------------------------------------------------------------------------
# Work — Inbox
# ---------------------------------------------------------------------------


class InboxItemResponse(BaseModel):
    """An inbox item — a vague capture awaiting triage."""

    id: str = Field(..., description="Inbox item ID")
    content: str = Field(..., description="Raw captured text")
    created: datetime | None = Field(None, description="Capture timestamp")
    source: str | None = Field(None, description="Where this came from", examples=["voice", "chat"])


class CreateInboxRequest(BaseModel):
    """Request body for adding to inbox."""

    content: str = Field(..., description="Raw text to capture", min_length=1)
    source: str | None = Field(None, description="Source of the capture")


# ---------------------------------------------------------------------------
# Work — Combined
# ---------------------------------------------------------------------------


class WorkResponse(BaseModel):
    """Full work state — tasks, projects, goals, inbox combined."""

    tasks: TaskListResponse = Field(default_factory=TaskListResponse)
    projects: ProjectListResponse = Field(default_factory=ProjectListResponse)
    goals: GoalListResponse = Field(default_factory=GoalListResponse)
    inbox: list[InboxItemResponse] = Field(default_factory=list)
    next_task: TaskResponse | None = Field(None, description="Suggested next task")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class OperatorResponse(BaseModel):
    """Operator configuration (from operator.yaml)."""

    name: str = Field(..., description="Operator name")
    timezone: str = Field("America/Chicago", description="IANA timezone")
    language: str = Field("en", description="Primary language")
    agent_name: str = Field("chief", description="Default agent")
    trust_default: TrustLevel = Field(TrustLevel.SURFACE, description="Default trust level")
    morning_briefing: str = Field("06:00", description="Morning briefing time")
    evening_checkin: str = Field("21:00", description="Evening check-in time")
    quiet_hours_start: str = Field("23:00", description="Quiet hours begin")
    quiet_hours_end: str = Field("06:00", description="Quiet hours end")
    business_type: str | None = Field(None, description="Business type")
    role: str | None = Field(None, description="Operator role")


class UpdateOperatorRequest(BaseModel):
    """Request body for updating operator config. All fields optional."""

    timezone: str | None = Field(None, description="New timezone")
    language: str | None = Field(None, description="New language")
    agent_name: str | None = Field(None, description="New default agent")
    trust_default: TrustLevel | None = Field(None, description="New default trust level")
    morning_briefing: str | None = Field(None, description="New briefing time")
    evening_checkin: str | None = Field(None, description="New check-in time")
    quiet_hours_start: str | None = Field(None, description="New quiet start")
    quiet_hours_end: str | None = Field(None, description="New quiet end")
    business_type: str | None = Field(None, description="New business type")
    role: str | None = Field(None, description="New role")


class AccountsResponse(BaseModel):
    """Accounts configuration — redacted, no secrets exposed."""

    accounts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of configured accounts with names and types (secrets redacted)",
    )
    total: int = Field(0, description="Total account count")


class IntegrationSummary(BaseModel):
    """Summary of a single integration (no secrets)."""

    id: str = Field(..., description="Integration identifier")
    name: str = Field(..., description="Display name")
    category: str = Field("", description="Category: marketing, finance, etc.")
    is_active: bool = Field(False, description="Whether integration is active")
    is_healthy: bool = Field(True, description="Last health check result")
    capabilities: list[str] = Field(default_factory=list, description="Provided capabilities")


class IntegrationsResponse(BaseModel):
    """Integration configuration and status."""

    integrations: list[IntegrationSummary] = Field(default_factory=list)
    total: int = Field(0, description="Total integration count")
    active_count: int = Field(0, description="Count of active integrations")


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentResponse(BaseModel):
    """An agent as returned by the API."""

    id: str = Field(..., description="Agent identifier", examples=["chief"])
    name: str = Field(..., description="Display name")
    domain: str = Field("", description="Agent's domain of expertise")
    description: str = Field("", description="What this agent does")
    model: str = Field("sonnet", description="LLM model used")

    tools: list[str] = Field(default_factory=list, description="Available tools")
    skills: list[str] = Field(default_factory=list, description="Available skills")

    default_trust: TrustLevel = Field(TrustLevel.SURFACE, description="Default trust level")

    is_system: bool = Field(False, description="System agent (chief, steward, advisor)")
    is_active: bool = Field(True, description="Whether agent is active")
    last_active: datetime | None = Field(None, description="Last activity timestamp")

    schedule: dict[str, str] = Field(default_factory=dict, description="Scheduled tasks")


class AgentListResponse(BaseModel):
    """List of all agents."""

    agents: list[AgentResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total agent count")
    active_count: int = Field(0, description="Active agents")
    system_count: int = Field(0, description="System agents")


class AgentCatalogResponse(BaseModel):
    """Available agents in the catalog (not yet activated)."""

    catalog: list[AgentResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total catalog entries")


class UpdateTrustRequest(BaseModel):
    """Request body for updating an agent's trust level."""

    action_type: str = Field(..., description="Action type to adjust trust for")
    trust_level: TrustLevel = Field(..., description="New trust level")
    reason: str | None = Field(None, description="Why trust is being changed")


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class SkillResponse(BaseModel):
    """A skill as returned by the API."""

    id: str = Field(..., description="Skill identifier (directory name)")
    name: str = Field(..., description="Skill display name")
    description: str = Field("", description="What this skill does")
    triggers: list[str] = Field(default_factory=list, description="Trigger phrases")
    is_active: bool = Field(True, description="Whether skill is enabled")
    source_path: str | None = Field(None, description="Path to SKILL.md")


class SkillListResponse(BaseModel):
    """List of all skills."""

    skills: list[SkillResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total skill count")
    active_count: int = Field(0, description="Active skills")


class ToggleSkillRequest(BaseModel):
    """Request body for enabling/disabling a skill."""

    enabled: bool = Field(..., description="Whether to enable or disable")


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


class ServiceResponse(BaseModel):
    """A service's status as returned by the API."""

    name: str = Field(..., description="Service name", examples=["bridge"])
    status: str = Field("unknown", description="Current status: running, stopped, error, unknown")
    port: int | None = Field(None, description="Port if network service")
    pid: int | None = Field(None, description="Process ID if running")
    uptime_seconds: float | None = Field(None, description="Seconds since start")
    last_check: datetime | None = Field(None, description="Last health check time")
    error: str | None = Field(None, description="Error message if unhealthy")


class ServiceListResponse(BaseModel):
    """List of all services with status."""

    services: list[ServiceResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total service count")
    healthy_count: int = Field(0, description="Healthy services")


class ServiceLogsResponse(BaseModel):
    """Recent log lines from a service."""

    service: str = Field(..., description="Service name")
    lines: list[str] = Field(default_factory=list, description="Log lines (newest last)")
    total_lines: int = Field(0, description="Total lines available")
    truncated: bool = Field(False, description="Whether output was truncated")


# ---------------------------------------------------------------------------
# Crons
# ---------------------------------------------------------------------------


class CronJobResponse(BaseModel):
    """A cron job definition and status."""

    name: str = Field(..., description="Job name", examples=["overnight"])
    schedule: str = Field(..., description="Cron expression", examples=["0 3 * * *"])
    command: str = Field("", description="Command or script path")
    enabled: bool = Field(True, description="Whether job is active")
    last_run: datetime | None = Field(None, description="Last execution time")
    last_status: str | None = Field(None, description="Last run result: success, failed")
    next_run: datetime | None = Field(None, description="Next scheduled execution")


class CronListResponse(BaseModel):
    """List of all cron jobs."""

    crons: list[CronJobResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total cron count")


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


class PersonResponse(BaseModel):
    """A person as returned by the API (list view)."""

    id: str = Field(..., description="Person identifier")
    name: str = Field(..., description="Full name")
    importance: int = Field(3, description="Importance level 1-4 (1 = most important)")
    privacy_level: int = Field(0, description="Privacy level 0-3")
    tags: list[str] = Field(default_factory=list, description="Tags")

    organization: str | None = Field(None, description="Organization")
    role: str | None = Field(None, description="Role or title")
    city: str | None = Field(None, description="City")

    last_contact: datetime | None = Field(None, description="Last interaction")
    days_since_contact: int | None = Field(None, description="Days since last contact")
    relationship_trend: str | None = Field(None, description="Trend: growing, stable, drifting")

    projects: list[str] = Field(default_factory=list, description="Linked project IDs")


class PersonListResponse(BaseModel):
    """Paginated list of people."""

    people: list[PersonResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total people count")
    page: int = Field(1, description="Current page")
    per_page: int = Field(50, description="Items per page")
    has_more: bool = Field(False, description="More pages available")


class InteractionSchema(BaseModel):
    """A single interaction/touchpoint with a person."""

    id: str = Field(..., description="Interaction ID")
    channel: ChannelType | None = Field(None, description="Communication channel")
    direction: str = Field("inbound", description="inbound or outbound")
    content_preview: str = Field("", description="Truncated content")
    timestamp: datetime | None = Field(None, description="When it happened")


class RelationshipSchema(BaseModel):
    """A relationship link between two people or a person and an entity."""

    link_type: str = Field(..., description="Relationship type")
    target_type: str = Field(..., description="Target entity type")
    target_id: str = Field(..., description="Target entity ID")
    target_name: str | None = Field(None, description="Target display name")


class PersonDetailResponse(PersonResponse):
    """Detailed person view with interactions and relationships."""

    email: str | None = Field(None, description="Email address")
    phone: str | None = Field(None, description="Phone number")
    how_met: str | None = Field(None, description="How you met")
    birthday: str | None = Field(None, description="Birthday")
    comms_trust_level: int = Field(0, description="Communications trust level")

    interactions: list[InteractionSchema] = Field(
        default_factory=list, description="Recent interactions"
    )
    relationships: list[RelationshipSchema] = Field(
        default_factory=list, description="Relationship links"
    )


class UpdatePersonRequest(BaseModel):
    """Request body for updating a person's fields. All optional."""

    name: str | None = Field(None, description="New name")
    importance: int | None = Field(None, description="New importance (1-4)", ge=1, le=4)
    privacy_level: int | None = Field(None, description="New privacy level (0-3)", ge=0, le=3)
    tags: list[str] | None = Field(None, description="Replace tags")
    organization: str | None = Field(None, description="Organization")
    role: str | None = Field(None, description="Role or title")
    city: str | None = Field(None, description="City")
    email: str | None = Field(None, description="Email")
    phone: str | None = Field(None, description="Phone")
    how_met: str | None = Field(None, description="How you met")
    birthday: str | None = Field(None, description="Birthday")


class PersonSearchRequest(BaseModel):
    """Request body for searching people."""

    query: str = Field(..., description="Search query string", min_length=1)
    tags: list[str] | None = Field(None, description="Filter by tags")
    importance_max: int | None = Field(None, description="Max importance (1 = most important)")
    project: str | None = Field(None, description="Filter by project")
    limit: int = Field(20, description="Max results", ge=1, le=100)


class PersonSurfaceItem(BaseModel):
    """An intelligence surface item — a person needing attention."""

    person: PersonResponse = Field(..., description="The person")
    reason: str = Field(..., description="Why they're surfaced")
    urgency: int = Field(3, description="Urgency 1-5")
    suggested_action: str | None = Field(None, description="What to do")


class PersonSurfaceResponse(BaseModel):
    """Intelligence queue — people needing attention."""

    surfaces: list[PersonSurfaceItem] = Field(default_factory=list)
    total: int = Field(0, description="Total items in queue")


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------


class VaultCollectionResponse(BaseModel):
    """A vault collection with document count."""

    name: str = Field(..., description="Collection name", examples=["knowledge"])
    path: str = Field(..., description="Filesystem path")
    doc_count: int = Field(0, description="Number of documents")
    last_indexed: datetime | None = Field(None, description="Last index time")


class VaultCollectionListResponse(BaseModel):
    """All vault collections."""

    collections: list[VaultCollectionResponse] = Field(default_factory=list)
    total_docs: int = Field(0, description="Total documents across all collections")


class VaultFileResponse(BaseModel):
    """A single vault file's content."""

    path: str = Field(..., description="Relative path within vault")
    title: str | None = Field(None, description="Frontmatter title")
    content: str = Field("", description="Raw markdown content")
    frontmatter: dict[str, Any] = Field(default_factory=dict, description="YAML frontmatter")
    size_bytes: int = Field(0, description="File size in bytes")


class VaultSearchResult(BaseModel):
    """A single search result from QMD."""

    path: str = Field(..., description="Document path")
    title: str | None = Field(None, description="Document title")
    snippet: str = Field("", description="Matching snippet")
    score: float = Field(0.0, description="Relevance score")
    collection: str = Field("", description="Which collection")


class VaultSearchResponse(BaseModel):
    """Search results from QMD."""

    results: list[VaultSearchResult] = Field(default_factory=list)
    total: int = Field(0, description="Total matches")
    query: str = Field("", description="Original query string")


class VaultSearchRequest(BaseModel):
    """Request body for vault search."""

    query: str = Field(..., description="Search query", min_length=1)
    collection: str | None = Field(None, description="Limit to collection")
    limit: int = Field(10, description="Max results", ge=1, le=50)
    min_score: float = Field(0.0, description="Minimum relevance score", ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


class SecretEntry(BaseModel):
    """A secret entry — name only, NEVER the value."""

    name: str = Field(..., description="Secret name / Keychain key")
    exists: bool = Field(True, description="Whether the secret exists in Keychain")
    last_rotated: datetime | None = Field(None, description="Last rotation date if tracked")


class SecretListResponse(BaseModel):
    """List of secret names — values are NEVER exposed."""

    secrets: list[SecretEntry] = Field(default_factory=list)
    total: int = Field(0, description="Total secret count")


class AddSecretRequest(BaseModel):
    """Request body for adding a secret to Keychain."""

    name: str = Field(..., description="Secret name", min_length=1)
    value: str = Field(..., description="Secret value (transmitted once, never stored in API)")


class RotateSecretRequest(BaseModel):
    """Request body for rotating a secret."""

    name: str = Field(..., description="Secret name to rotate")
    new_value: str = Field(..., description="New secret value")


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """System health check response."""

    status: str = Field("healthy", description="Overall status: healthy, degraded, down")
    uptime_seconds: float = Field(0.0, description="System uptime")
    services: dict[str, str] = Field(
        default_factory=dict, description="Per-service status map"
    )
    timestamp: datetime | None = Field(None, description="Check timestamp")
    errors: list[str] = Field(default_factory=list, description="Active errors")


class StorageDevice(BaseModel):
    """Storage device usage."""

    name: str = Field(..., description="Device name", examples=["internal", "AOS-X"])
    total_gb: float = Field(0.0, description="Total capacity in GB")
    used_gb: float = Field(0.0, description="Used space in GB")
    free_gb: float = Field(0.0, description="Free space in GB")
    usage_percent: float = Field(0.0, description="Usage as percentage")


class SymlinkResponse(BaseModel):
    """Status of a managed symlink."""

    source: str = Field(..., description="Symlink path", examples=["~/vault"])
    target: str = Field(..., description="Target path", examples=["/Volumes/AOS-X/vault"])
    valid: bool = Field(True, description="Whether symlink resolves correctly")
    error: str | None = Field(None, description="Error if broken")


class StorageResponse(BaseModel):
    """Disk usage and symlink status."""

    internal: StorageDevice = Field(..., description="Internal SSD stats")
    external: StorageDevice | None = Field(None, description="AOS-X external SSD stats")
    symlinks: list[SymlinkResponse] = Field(default_factory=list, description="Symlink status")


class ReconcileCheckResult(BaseModel):
    """Result of a single reconcile check."""

    name: str = Field(..., description="Check name")
    passed: bool = Field(True, description="Whether check passed")
    message: str = Field("", description="Human-readable result")


class ReconcileResponse(BaseModel):
    """Results from the last reconcile run."""

    checks: list[ReconcileCheckResult] = Field(default_factory=list)
    passed: int = Field(0, description="Checks that passed")
    failed: int = Field(0, description="Checks that failed")
    run_at: datetime | None = Field(None, description="When reconcile ran")


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class ChannelStatusResponse(BaseModel):
    """A communication channel's status."""

    id: str = Field(..., description="Channel identifier")
    channel_type: ChannelType = Field(..., description="Channel type")
    name: str = Field(..., description="Display name")
    is_active: bool = Field(True, description="Whether channel is active")
    is_healthy: bool = Field(True, description="Current health status")
    last_checked: datetime | None = Field(None, description="Last health check")
    messages_today: int = Field(0, description="Messages processed today")
    last_message: datetime | None = Field(None, description="Most recent message time")


class ChannelListResponse(BaseModel):
    """All communication channels."""

    channels: list[ChannelStatusResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total channels")
    active_count: int = Field(0, description="Active channels")
    healthy_count: int = Field(0, description="Healthy channels")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class MetricDataPoint(BaseModel):
    """A single metric data point."""

    timestamp: datetime = Field(..., description="When the metric was recorded")
    value: float = Field(..., description="Metric value")
    labels: dict[str, str] = Field(default_factory=dict, description="Metric labels")


class MetricResponse(BaseModel):
    """A named metric with recent data points."""

    name: str = Field(..., description="Metric name", examples=["tasks.completed.daily"])
    description: str = Field("", description="What this metric measures")
    unit: str = Field("", description="Unit of measurement", examples=["count", "ms", "percent"])
    current_value: float | None = Field(None, description="Most recent value")
    data_points: list[MetricDataPoint] = Field(
        default_factory=list, description="Historical data points"
    )


class MetricListResponse(BaseModel):
    """List of all tracked metrics."""

    metrics: list[MetricResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total metrics tracked")


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


class PipelineStageSchema(BaseModel):
    """A single stage definition within a pipeline."""

    name: str = Field(..., description="Stage name")
    handler: str = Field("", description="Handler function or module")
    timeout_seconds: int = Field(300, description="Stage timeout")


class PipelineDefinition(BaseModel):
    """A pipeline definition."""

    name: str = Field(..., description="Pipeline name", examples=["message_intake"])
    description: str = Field("", description="What this pipeline does")
    stages: list[PipelineStageSchema] = Field(default_factory=list, description="Ordered stages")
    is_active: bool = Field(True, description="Whether pipeline is active")


class PipelineListResponse(BaseModel):
    """List of all pipeline definitions."""

    pipelines: list[PipelineDefinition] = Field(default_factory=list)
    total: int = Field(0, description="Total pipelines")


class PipelineRunResponse(BaseModel):
    """A single pipeline execution run."""

    run_id: str = Field(..., description="Unique run identifier")
    pipeline_name: str = Field(..., description="Pipeline name")
    status: PipelineStage = Field(PipelineStage.QUEUED, description="Run status")
    started: datetime | None = Field(None, description="Start time")
    completed: datetime | None = Field(None, description="Completion time")
    current_stage: str | None = Field(None, description="Stage currently executing")
    stages_completed: int = Field(0, description="Number of stages completed")
    total_stages: int = Field(0, description="Total stages in pipeline")
    error: str | None = Field(None, description="Error message if failed")
    entity_id: str | None = Field(None, description="Entity being processed")
    duration_ms: float | None = Field(None, description="Total duration in milliseconds")


class PipelineRunListResponse(BaseModel):
    """List of pipeline runs."""

    runs: list[PipelineRunResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total runs")
    pipeline_name: str = Field("", description="Pipeline these runs belong to")

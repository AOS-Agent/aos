"""Qareen Ontology — Object Types and Relationships.

Every entity in AOS is modeled as a typed object with properties and
relationships. This file defines the complete data model. All other
components import from here.

Storage is heterogeneous (SQLite, YAML, markdown). The ontology is a
semantic layer that connects everything — it doesn't own the storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ObjectType(str, Enum):
    PERSON = "person"
    TASK = "task"
    PROJECT = "project"
    GOAL = "goal"
    MESSAGE = "message"
    NOTE = "note"
    DECISION = "decision"
    SESSION = "session"
    AGENT = "agent"
    CHANNEL = "channel"
    INTEGRATION = "integration"
    # New types from research
    AREA = "area"
    WORKFLOW = "workflow"
    WORKFLOW_RUN = "workflow_run"
    PIPELINE_ENTRY = "pipeline_entry"
    REMINDER = "reminder"
    TRANSACTION = "transaction"
    PROCEDURE = "procedure"
    CONVERSATION = "conversation"


class TaskStatus(str, Enum):
    TODO = "todo"
    ACTIVE = "active"
    WAITING = "waiting"
    DONE = "done"


class TaskPriority(int, Enum):
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4
    SOMEDAY = 5


class MessageDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ChannelType(str, Enum):
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    SLACK = "slack"
    SMS = "sms"


class NoteStage(int, Enum):
    CAPTURE = 1
    TRIAGE = 2
    RESEARCH = 3
    SYNTHESIS = 4
    DECISION = 5
    EXPERTISE = 6


class TrustLevel(int, Enum):
    OBSERVE = 0
    SURFACE = 1
    DRAFT = 2
    ACT_WITH_DIGEST = 3
    ACT_WITH_AUDIT = 4
    AUTONOMOUS = 5


class SessionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class PipelineStage(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"


# ---------------------------------------------------------------------------
# Link types — typed relationships between objects
# ---------------------------------------------------------------------------

class LinkType(str, Enum):
    # Person links
    ASSIGNED_TO = "assigned_to"
    MENTIONED_IN = "mentioned_in"
    CREATED_BY = "created_by"
    SENT_BY = "sent_by"
    SENT_TO = "sent_to"
    ABOUT = "about"
    MEMBER_OF = "member_of"
    CLIENT_OF = "client_of"
    REPORTS_TO = "reports_to"
    KNOWS = "knows"
    REFERRED_BY = "referred_by"

    # Task links
    BELONGS_TO = "belongs_to"
    BLOCKS = "blocks"
    SUBTASK_OF = "subtask_of"
    WORKED_ON_IN = "worked_on_in"
    RESULTED_IN = "resulted_in"

    # Note links
    MENTIONS = "mentions"
    REFERENCES = "references"
    LINKS_TO = "links_to"
    SCOPED_TO = "scoped_to"

    # Channel/Integration links
    RECEIVED_VIA = "received_via"
    USES = "uses"

    # Session links
    RUN_BY = "run_by"
    PARTICIPANTS = "participants"


@dataclass
class Link:
    """A typed relationship between two objects."""
    link_type: LinkType
    source_type: ObjectType
    source_id: str
    target_type: ObjectType
    target_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Object types — the ontology entities
# ---------------------------------------------------------------------------

@dataclass
class Person:
    id: str
    name: str
    importance: int = 3  # 1-4, 1 = most important
    privacy_level: int = 0  # 0 = open, 3 = no AI analysis
    tags: list[str] = field(default_factory=list)

    # Contact info (from person_identifiers table)
    email: str | None = None
    phone: str | None = None
    whatsapp_jid: str | None = None
    telegram_id: str | None = None

    # Metadata (from contact_metadata table)
    organization: str | None = None
    role: str | None = None
    city: str | None = None
    how_met: str | None = None
    birthday: str | None = None

    # Relationship state (computed)
    last_contact: datetime | None = None
    days_since_contact: int | None = None
    relationship_trend: str | None = None  # growing, stable, drifting

    # Trust (from comms trust system)
    comms_trust_level: int = 0

    # Projects this person is linked to
    projects: list[str] = field(default_factory=list)


@dataclass
class Task:
    id: str  # project-scoped: "nuchay#42"
    title: str
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.NORMAL
    project: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None

    # Assignment
    assigned_to: str | None = None  # person or agent id
    created_by: str | None = None

    # Dates
    created: datetime | None = None
    started: datetime | None = None
    completed: datetime | None = None
    due: datetime | None = None

    # Hierarchy
    parent_id: str | None = None
    subtask_ids: list[str] = field(default_factory=list)

    # Handoff (for agent continuity)
    handoff: TaskHandoff | None = None

    # Pipeline state (if this task is being processed)
    pipeline: str | None = None
    pipeline_stage: PipelineStage | None = None

    # Recurrence
    recurrence: str | None = None  # cron expression or None

    # Execution context (from Tadbir research)
    context: str | None = None  # GTD context: @computer, @phone, @office, @errands
    energy: str | None = None  # high, medium, low — what energy level this needs
    quality_standard: str | None = None  # what "done well" looks like
    time_estimate: str | None = None  # ISO 8601 duration estimate
    actual_time: str | None = None  # actual time spent
    area_id: str | None = None  # which Area this belongs to (if not in a project)


@dataclass
class TaskHandoff:
    state: str
    next_step: str
    files: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    session_id: str | None = None
    timestamp: datetime | None = None


@dataclass
class Project:
    id: str
    title: str
    description: str | None = None
    status: str = "active"  # active, paused, completed, archived
    path: str | None = None  # filesystem path if applicable
    goal: str | None = None
    done_when: str | None = None

    # Telegram routing
    telegram_bot_key: str | None = None
    telegram_chat_key: str | None = None
    telegram_forum_topic: int | None = None

    # Stage pipeline (for initiative-type projects)
    stages: list[str] | None = None  # e.g. ["research", "shaping", "planning", "executing"]
    current_stage: str | None = None

    # Metrics
    task_count: int = 0
    done_count: int = 0
    active_count: int = 0


@dataclass
class Goal:
    id: str
    title: str
    weight: int = 0  # percentage weight, all goals should sum to 100
    description: str | None = None
    key_results: list[KeyResult] = field(default_factory=list)
    project: str | None = None


@dataclass
class KeyResult:
    title: str
    progress: int = 0  # 0-100
    target: str | None = None


@dataclass
class Message:
    id: str
    channel: ChannelType
    direction: MessageDirection
    sender_id: str | None = None  # person id
    recipient_id: str | None = None  # person id
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    # Thread/conversation tracking
    thread_id: str | None = None
    reply_to_id: str | None = None

    # Attachments
    has_attachment: bool = False
    attachment_type: str | None = None  # image, document, audio, video
    attachment_path: str | None = None

    # Processing state
    processed: bool = False
    extracted_entities: list[str] = field(default_factory=list)

    # Channel-specific metadata
    channel_metadata: dict[str, Any] = field(default_factory=dict)

    # Resolved fields (from comms research)
    person_id: str | None = None  # resolved FK to people.db
    conversation_id: str | None = None  # FK to conversations
    intent: str | None = None  # request, info, social, commitment, question
    urgency: int = 0  # 0-3 score


@dataclass
class Note:
    id: str  # vault path relative to ~/vault/
    title: str
    note_type: str = "capture"  # capture, research, reference, synthesis, decision, expertise
    stage: NoteStage = NoteStage.CAPTURE
    date: datetime | None = None
    tags: list[str] = field(default_factory=list)
    project: str | None = None
    source_ref: str | None = None

    # Content
    content: str = ""  # raw markdown
    summary: str | None = None  # AI-generated summary

    # Extracted entities
    mentioned_people: list[str] = field(default_factory=list)
    referenced_tasks: list[str] = field(default_factory=list)
    linked_notes: list[str] = field(default_factory=list)

    # Authority dimension (from knowledge research)
    audience: str = "personal"  # personal, team, public
    verified_by: str | None = None
    verified_date: datetime | None = None
    review_interval_days: int | None = None
    next_review: datetime | None = None
    is_archived: bool = False


@dataclass
class Decision:
    """A locked conclusion — a Note with type=decision and extra structure."""
    id: str
    title: str
    rationale: str = ""
    date: datetime | None = None
    stakeholders: list[str] = field(default_factory=list)  # person ids
    project: str | None = None
    tags: list[str] = field(default_factory=list)
    supersedes: str | None = None  # id of previous decision this replaces
    status: str = "active"  # active, superseded, revisiting


@dataclass
class Session:
    id: str
    agent_id: str | None = None
    operator_id: str | None = None  # for multi-operator
    status: SessionStatus = SessionStatus.ACTIVE
    started: datetime = field(default_factory=datetime.now)
    ended: datetime | None = None

    # What was accomplished
    tasks_completed: list[str] = field(default_factory=list)
    tasks_created: list[str] = field(default_factory=list)
    decisions_made: list[str] = field(default_factory=list)
    outcome: str | None = None

    # Transcript (compressed)
    transcript_summary: str | None = None
    utterance_count: int = 0

    # Context
    project: str | None = None
    thread_id: str | None = None


@dataclass
class Agent:
    id: str  # e.g. "marketing", "accounting", "chief"
    name: str
    domain: str = ""
    description: str = ""
    model: str = "sonnet"  # default LLM model

    # Capabilities
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)

    # Trust (per-capability trust stored separately)
    default_trust: TrustLevel = TrustLevel.SURFACE

    # Schedule
    schedule: dict[str, str] = field(default_factory=dict)  # e.g. {"daily": "check performance"}

    # State
    is_system: bool = False  # chief, steward, advisor
    is_active: bool = True
    last_active: datetime | None = None

    # Source
    source_path: str | None = None  # path to agent .md file


@dataclass
class Channel:
    id: str
    channel_type: ChannelType
    name: str
    is_active: bool = True
    is_healthy: bool = True
    last_checked: datetime | None = None

    # Connection details (never exposed to frontend)
    config: dict[str, Any] = field(default_factory=dict)

    # Stats
    messages_today: int = 0
    last_message: datetime | None = None


@dataclass
class Integration:
    id: str
    name: str
    category: str = ""  # marketing, finance, commerce, research, productivity
    is_active: bool = False
    is_healthy: bool = True
    last_checked: datetime | None = None

    # Capabilities this integration provides
    capabilities: list[str] = field(default_factory=list)

    # Config (auth info is in Keychain, not here)
    config: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# External intelligence
# ---------------------------------------------------------------------------

class IntelligenceLayer(int, Enum):
    INTERNAL = 0
    NETWORK = 1
    HYPER_LOCAL = 2
    LOCAL_REGIONAL = 3
    INDUSTRY = 4
    GLOBAL = 5


class AcquisitionTier(str, Enum):
    API = "api"
    FIRECRAWL = "firecrawl"
    SOCIAL = "social"
    RESTRICTED = "restricted"


@dataclass
class IntelligenceSource:
    id: str
    name: str
    layer: IntelligenceLayer
    tier: AcquisitionTier
    url: str | None = None
    update_cadence: str = "daily"  # hourly, daily, weekly, on_demand
    is_active: bool = True
    category: str = ""  # weather, market, regulation, competitor, news, social
    project: str | None = None  # scoped to project or None for global
    last_checked: datetime | None = None
    consecutive_failures: int = 0
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntelligenceBrief:
    id: str
    source_id: str
    layer: IntelligenceLayer
    category: str
    title: str
    summary: str  # LLM-synthesized analysis
    key_findings: list[str] = field(default_factory=list)
    relevance_score: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    project: str | None = None
    surfaced: bool = False
    operator_action: str | None = None  # acknowledged, acted_on, dismissed


# ---------------------------------------------------------------------------
# Self-improvement
# ---------------------------------------------------------------------------

class FrictionCategory(str, Enum):
    REPEATED_MANUAL = "repeated_manual"
    ERROR_PATTERN = "error_pattern"
    SLOW_PATH = "slow_path"
    OPERATOR_CORRECTION = "operator_correction"


@dataclass
class FrictionEntry:
    id: int
    category: FrictionCategory
    description: str
    frequency: int = 1
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    source: str = ""  # session_analysis, error_log, operator_feedback
    status: str = "open"  # open, proposed, resolved, dismissed


@dataclass
class ImprovementProposal:
    id: str
    title: str
    description: str
    proposal_type: str  # new_skill, new_pipeline, config_change, integration, architectural
    estimated_effort: str = "medium"  # low, medium, high
    estimated_value: str = "medium"  # low, medium, high, critical
    implementation: dict[str, Any] = field(default_factory=dict)
    status: str = "proposed"  # proposed, approved, implementing, completed, rejected, deferred
    friction_id: int | None = None
    deliberation_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    verified: bool = False


@dataclass
class Deliberation:
    id: str
    topic: str
    context: dict[str, Any] = field(default_factory=dict)
    time_cap_seconds: int = 600
    token_cap: int = 50000
    tokens_used: int = 0
    perspectives: list[dict[str, Any]] = field(default_factory=list)
    recommendation: str = ""
    confidence: float = 0.0
    dissenting_views: list[str] = field(default_factory=list)
    status: str = "pending"  # pending, in_progress, completed, timed_out
    triggered_by: str = "operator"  # operator, self_improvement, overnight
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class SkillProposal:
    id: str
    title: str
    description: str
    trigger_phrases: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    estimated_value: str = "medium"
    detection_source: str = ""  # session_analysis, operator_request, template, friction
    evidence: dict[str, Any] = field(default_factory=dict)
    status: str = "proposed"  # proposed, approved, building, deployed, rejected, failed
    skill_path: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    usage_count: int = 0
    success_rate: float = 0.0


# ---------------------------------------------------------------------------
# Work types (from Tadbir research)
# ---------------------------------------------------------------------------

@dataclass
class Area:
    """A permanent domain of responsibility. Never completed — only maintained."""
    id: str
    name: str
    standard: str = ""  # what "healthy" looks like for this area
    review_cadence: str = "weekly"
    parent_id: str | None = None  # areas can nest (Marketing > Content Marketing)
    is_active: bool = True
    metrics: list[dict[str, Any]] = field(default_factory=list)  # tracked KPIs for this area


@dataclass
class Workflow:
    """A reusable template that generates Tasks (and optionally a Project) when triggered."""
    id: str
    name: str
    description: str = ""
    trigger_type: str = "manual"  # manual, scheduled, event
    trigger_config: dict[str, Any] = field(default_factory=dict)  # cron string, event type, etc.
    task_templates: list[dict[str, Any]] = field(default_factory=list)  # tasks to create
    project_template: dict[str, Any] | None = None  # optional project to create
    assignee_defaults: dict[str, str] = field(default_factory=dict)
    is_active: bool = True
    run_count: int = 0
    last_run: datetime | None = None


@dataclass
class WorkflowRun:
    """A single execution of a Workflow."""
    id: str
    workflow_id: str
    status: str = "running"  # running, completed, failed, cancelled
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    project_id: str | None = None  # created project, if any
    task_ids: list[str] = field(default_factory=list)  # created tasks
    triggered_by: str = "operator"  # operator, agent, schedule, event
    trigger_event: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# People types (from CRM research)
# ---------------------------------------------------------------------------

@dataclass
class PipelineEntry:
    """A person's position in a named process (sales, hiring, donor cultivation)."""
    id: str
    person_id: str
    pipeline_name: str  # "sales", "hiring", "donor_cultivation", etc.
    stage: str  # current stage name
    value: float = 0.0  # monetary value (deal size, donation amount)
    currency: str = "CAD"
    entered_at: datetime = field(default_factory=datetime.now)
    last_moved_at: datetime | None = None
    expected_close: datetime | None = None
    owner: str | None = None  # person or agent responsible
    project_id: str | None = None  # linked project
    notes: str = ""


@dataclass
class PipelineDefinition:
    """Definition of a pipeline with ordered stages."""
    name: str
    description: str = ""
    stages: list[str] = field(default_factory=list)  # ordered stage names
    default_stage: str = ""  # where new entries start
    closed_won_stages: list[str] = field(default_factory=list)  # "won", "hired", "recurring_donor"
    closed_lost_stages: list[str] = field(default_factory=list)  # "lost", "rejected", "lapsed"


@dataclass
class Reminder:
    """A scheduled follow-up linked to a person."""
    id: str
    person_id: str
    due_date: datetime
    note: str = ""
    recurrence: str | None = None  # cron expression for recurring reminders
    status: str = "pending"  # pending, done, snoozed, cancelled
    snoozed_until: datetime | None = None
    created_by: str = "operator"
    task_id: str | None = None  # optionally linked to a task


@dataclass
class Transaction:
    """A financial record linked to a person."""
    id: str
    person_id: str
    amount: float
    currency: str = "CAD"
    transaction_type: str = "payment"  # payment, donation, invoice, refund, subscription
    date: datetime = field(default_factory=datetime.now)
    status: str = "completed"  # pending, completed, failed, refunded
    description: str = ""
    project_id: str | None = None
    external_ref: str | None = None  # reference to external system (Stripe, PayPal, Wave)


# ---------------------------------------------------------------------------
# Knowledge types (from vault research)
# ---------------------------------------------------------------------------

@dataclass
class Procedure:
    """An executable SOP — step-by-step, not prose."""
    id: str
    title: str
    description: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)  # ordered steps with action, params
    owner: str | None = None
    review_interval_days: int = 90
    last_reviewed: datetime | None = None
    next_review: datetime | None = None
    linked_workflow: str | None = None  # workflow that executes this procedure
    project: str | None = None
    tags: list[str] = field(default_factory=list)
    version: int = 1


# ---------------------------------------------------------------------------
# Communication types (from comms research)
# ---------------------------------------------------------------------------

@dataclass
class Conversation:
    """A cross-channel thread with a person."""
    id: str
    channel: ChannelType
    person_id: str | None = None  # resolved contact
    name: str = ""  # display name
    status: str = "open"  # open, snoozed, archived
    last_message_at: datetime | None = None
    message_count: int = 0
    unread_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Context card — pre-built context for real-time surfacing
# ---------------------------------------------------------------------------

@dataclass
class ContextCard:
    """Pre-built context summary for an entity. Built overnight,
    surfaced in <100ms when the entity is mentioned."""
    entity_type: ObjectType
    entity_id: str
    summary: str  # 2-3 sentence natural language summary
    key_facts: list[str] = field(default_factory=list)
    recent_activity: list[str] = field(default_factory=list)
    open_items: list[str] = field(default_factory=list)
    built_at: datetime = field(default_factory=datetime.now)
    stale_after: datetime | None = None  # when to rebuild


# ---------------------------------------------------------------------------
# Action result — returned by every governed mutation
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    success: bool
    action_name: str
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None  # the created/modified object
    error: str | None = None
    event_emitted: str | None = None  # event type that was emitted
    audit_id: str | None = None  # reference to audit log entry


# ---------------------------------------------------------------------------
# Operator — the human running the system
# ---------------------------------------------------------------------------

@dataclass
class Operator:
    name: str
    timezone: str = "America/Chicago"
    language: str = "en"
    agent_name: str = "chief"
    trust_default: TrustLevel = TrustLevel.SURFACE

    # Schedule
    morning_briefing: str = "06:00"
    evening_checkin: str = "21:00"
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "06:00"

    # Business context
    business_type: str | None = None  # ecommerce, nonprofit, consultant, agency, solo
    role: str | None = None


# ---------------------------------------------------------------------------
# Trust entry — per (agent, action_type) trust level
# ---------------------------------------------------------------------------

@dataclass
class TrustEntry:
    agent_id: str
    action_type: str
    trust_level: TrustLevel
    acceptance_rate: float = 0.0  # trailing acceptance rate
    total_actions: int = 0
    last_promoted: datetime | None = None
    last_demoted: datetime | None = None
    circuit_breaker_open: bool = False

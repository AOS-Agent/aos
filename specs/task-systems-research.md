# Task Systems Research: Architecture, Data Models, and Design Decisions

Deep technical research into how leading agentic systems and task management platforms structure their internals. Implementation-level detail, not marketing.

---

## 1. Manus Agent Architecture

Manus (acquired by Meta ~$2B) was built on Claude Sonnet with 28 tools, not as a standalone model but as an orchestration layer. No multi-agent internally — single agent with tool dispatch.

### The Agent Loop

Iterative cycle with one tool action per iteration:

```
1. Analyze Events    → Read event stream (chronological log of messages, actions, observations)
2. Select Tools      → Choose one tool action based on current state
3. Wait for Execution → Execute in sandbox, await result
4. Observe           → Result appended to event stream
5. Iterate           → Repeat until task complete
6. Submit Results    → Deliver with attachments
7. Enter Standby     → Wait for new task (idle tool)
```

Critical constraint: **one tool per iteration**. The agent must observe the result before deciding the next step. This prevents unchecked execution chains.

### The todo.md Protocol

Manus creates and maintains a `todo.md` file as a persistent checklist. This is a deliberate **attention manipulation mechanism** — by rewriting the todo list, Manus "recites objectives into the end of context," combating the lost-in-the-middle problem during 50+ tool call sequences.

Format follows standard TODO.md:
```markdown
# TODO

## Phase 1: Research
- [x] Gather data from sources
- [x] Validate findings
- [ ] Compile analysis

## Phase 2: Implementation
- [ ] Build prototype
- [ ] Run tests
```

The agent updates checkboxes after each step completion. When planning changes significantly, it rebuilds the entire file. It verifies all items are completed before calling the `idle` tool.

### The 3-File Pattern (from reverse-engineering)

Beyond todo.md, Manus uses three persistent files:

| File | Purpose |
|------|---------|
| `task_plan.md` | Phases, goals, completion checkboxes |
| `findings.md` | Research, discoveries, accumulated knowledge |
| `progress.md` | Session log, test results, error tracking |

Philosophy: **Context Window = RAM (volatile, limited) / Filesystem = Disk (persistent, unlimited)**. Anything important gets written to disk.

### Complete Tool List (28 tools)

**Communication (2):**
- `message_notify_user` — non-blocking progress updates with optional attachments
- `message_ask_user` — blocking questions requiring user response

**File Operations (5):**
- `file_read` — read content with line range support
- `file_write` — create or append content
- `file_str_replace` — replace specific strings
- `file_find_in_content` — regex search across files
- `file_find_by_name` — glob pattern file location

**Shell (5):**
- `shell_exec` — execute commands in shell sessions
- `shell_view` — view session output
- `shell_wait` — wait for process completion
- `shell_write_to_process` — send input to running processes
- `shell_kill_process` — terminate processes

**Browser (12):**
- `browser_view`, `browser_navigate`, `browser_restart`
- `browser_click`, `browser_input`, `browser_move_mouse`
- `browser_press_key`, `browser_select_option`
- `browser_scroll_up`, `browser_scroll_down`
- `browser_console_exec`, `browser_console_view`

**Search (1):**
- `info_search_web` — web search with date filtering

**Deploy (3):**
- `deploy_expose_port` — temporary public access
- `deploy_apply_deployment` — deploy static/Next.js sites
- `make_manus_page` — deploy from MDX files

**Control (1):**
- `idle` — signal task completion

### When to Ask vs Proceed Autonomously

Two distinct communication modes:
- **notify** (non-blocking): Progress updates. Agent continues working.
- **ask** (blocking): Questions requiring user input. Agent pauses.

Rules from the system prompt:
- Reply immediately to new user messages before other operations
- Use `ask` only when blocking input is truly necessary
- Proceed autonomously on standard tasks
- Request confirmation for sensitive/irreversible operations

### Context Engineering (from Manus's own blog post)

Key architectural decisions:

**KV-Cache as primary optimization metric.** Input-to-output ratio is 100:1, so prefix caching is critical. Cached tokens cost ~$0.30/MTok vs $3/MTok uncached. Design decisions:
- Stable prompt prefixes (no timestamps or mutable data that invalidate cache)
- Append-only contexts with deterministic JSON serialization
- Session routing via session IDs across distributed vLLM workers

**Tool masking instead of tool removal.** Rather than dynamically adding/removing tools (which breaks KV-cache), Manus uses logit masking at decode time. Three modes:
- Auto (optional function calls)
- Required (mandatory calls, any function)
- Specified (constrained to function subsets)

Tool names use consistent prefixes (`browser_`, `shell_`, `file_`) enabling stateless constraint enforcement without modifying context.

**Error preservation.** Failed actions and stack traces stay in context. The model implicitly updates beliefs and avoids repeating mistakes.

**Context diversity.** Structured variation across serialization templates prevents pattern-locking during repetitive tasks.

### Sandbox Environment

Isolated Ubuntu Linux VM with:
- Full internet access
- Shell with sudo
- Headless browser (Chromium)
- Python and Node.js interpreters
- File system access
- Can launch web servers and expose them publicly

### Model Architecture

Multi-model: Claude 3.5 Sonnet + fine-tuned Alibaba Qwen models. "Multi-model dynamic invocation" routes different task types to optimal models.

### Open Source Clone: OpenManus

Inheritance hierarchy:
```
BaseAgent (abstract)
├── ReActAgent (thought generation)
│   └── ToolCallAgent (tool execution)
│       ├── Manus (general-purpose)
│       ├── MCPAgent (MCP-focused)
│       ├── BrowserAgent (web automation)
│       ├── DataAnalysis (data tasks)
│       └── SandboxManus (isolated execution)
```

Each step: `think()` → `act()` → `observe()`. The step method repeats until the plan is complete.

PlanningAgent responsibilities:
- Create structured plans from user input
- Record execution status of each step
- Auto-mark current active step and update completion status

---

## 2. Claude Code's Internal Task Management

### TodoWrite Schema

```typescript
interface TodoItem {
  id: string;           // Unique identifier (min length 3)
  content: string;      // Imperative form: "Run tests"
  activeForm: string;   // Present continuous: "Running tests"
  status: 'pending' | 'in_progress' | 'completed';
  priority: 'high' | 'medium' | 'low';
}

interface TodoWriteParams {
  todos: TodoItem[];
}

interface TodoWriteResponse {
  success: boolean;
  count: number;
}
```

### Activation Rules

Use TodoWrite when:
- Task requires 3+ distinct steps
- Non-trivial, planning-intensive work
- User explicitly asks for a todo list
- Multiple assignments (numbered or comma-separated)

Skip when:
- Single, straightforward task
- Fewer than 3 simple steps
- Purely conversational

### Critical Constraints

- **Exactly ONE task must be `in_progress` at any time** — single-threaded execution
- Mark tasks complete IMMEDIATELY after finishing (no batching)
- Only mark complete when FULLY accomplished
- If blocked, keep `in_progress` and create new task describing the blocker
- Never complete tasks with failing tests, partial implementation, or unresolved errors

### Tasks API (v2.1.16+)

Evolution beyond TodoWrite:
- **Persistent storage** (survives across sessions)
- **Dependency tracking** between tasks
- **Multi-session support**
- Old TodoWrite still available via opt-out flag

### Multi-File Change Tracking

Claude Code tracks changes through:
1. **Git integration** — `git status` and `git diff` for repository state
2. **File-level references** — `file_path:line_number` patterns for precise location
3. **Edit tool constraint** — requires prior `Read` of a file before `Edit`, maintaining change awareness
4. **Sub-agents** — isolated conversation scopes for complex operations, returning concise summaries (saves tokens by not storing intermediate tool calls)

### CLAUDE.md Pattern

Persistent project context file that:
- Lives at project root, checked into the codebase
- Contains project-specific commands, conventions, rules
- Loaded automatically at session start as part of the system prompt
- Hierarchical: root `~/CLAUDE.md` → project `./CLAUDE.md` → nested `.claude/` directories
- Agent suggests writing useful patterns to CLAUDE.md for future sessions

### Session Architecture

Three-stage pipeline:
1. **Quota checking** (Haiku model)
2. **Topic detection**
3. **Main agent processing**

Session memory: summarized context from past sessions appended with note: "These session summaries are from PAST sessions that might not be related to the current task."

Context compression strategies:
- **Compact tool** — Sonnet model creates detailed summaries preserving technical specifics
- **Bash output summarization** — verbose logs auto-summarized, error details preserved
- **Agent isolation** — sub-agent contexts separate from main agent

### Planning Mode

`ExitPlanMode` tool signals transition from planning to implementation. Only activates for tasks requiring code implementation planning.

### Reasoning Intensity

Regex pattern matching on user input detects desired thinking depth:
- "think harder" → extended reasoning
- "megathink" → deeper analysis
- "ultrathink" → maximum reasoning depth

---

## 3. Linear's Data Model

### Conceptual Hierarchy

```
Workspace (organization)
├── Teams (groups of people)
│   ├── Issues (tasks)
│   │   ├── Sub-issues (nested)
│   │   ├── Relations (blocking/blocked)
│   │   ├── Labels
│   │   ├── Attachments
│   │   └── Comments
│   ├── Cycles (recurring sprints)
│   └── Workflows (status definitions)
├── Projects (cross-team, time-bound)
│   └── Milestones
├── Initiatives (curated list of projects)
└── Views (dynamic filtered queries)
```

### Issue Properties

Required: title, status
Optional: priority, estimate, label, due date, assignee
Identifier format: `TeamID-Number` (e.g., `ENG-123`)
Must belong to a single team.

### Priority Model

Five levels (numeric values from API):
| Value | Label | Meaning |
|-------|-------|---------|
| 0 | No priority | Unset |
| 1 | Urgent | Needs immediate attention |
| 2 | High | Important, address soon |
| 3 | Medium | Standard priority |
| 4 | Low | Address when possible |

Priority is also available at the Project level (added July 2024).

### State Categories (fixed order, custom names within)

| Category | Type | Examples |
|----------|------|----------|
| Triage | triage | Triage |
| Backlog | backlog | Backlog |
| Unstarted | unstarted | Todo, Ready |
| Started | started | In Progress, In Review |
| Completed | completed | Done, Merged |
| Canceled | canceled | Canceled, Duplicate, Won't Fix |

Rules:
- Categories are fixed and ordered; states rearrange only within their category
- At least one state must exist in each category
- Default state (first Backlog item) applies to newly created issues
- Triage is optional, enabled per-team

### Cycles (Sprints)

- Recurring, time-bound issue groupings
- Automated start/end
- Progress calculated as: `(completed_points + 0.25 * in_progress_points) / total_points`
- Cycle automations move issues to/from backlog automatically

### Projects vs Initiatives

- **Projects** = "specific, time-bound deliverable" — can span teams, contain milestones
- **Initiatives** = "manually curated list of projects" — workspace-level organizational goals

### Issue Relations

- Blocking / blocked by
- Sub-issues (hierarchical nesting, unlimited depth)
- Duplicate marking (converts to Canceled, transfers attachments)

### Triage Workflow

Triage is a specialized inbox. Issues enter triage when:
- Created through integrations (Slack, Sentry, etc.)
- Submitted by workspace members outside the specific team
- Generated in the Triage view

Four triage actions:
1. **Accept** (`1`) — moves to team's default status
2. **Mark as Duplicate** (`2`) — merges into existing issue, cancels original
3. **Decline** (`3`) — sets to Canceled with optional explanation
4. **Snooze** (`H`) — hides until selected timeframe or new activity

**Triage Intelligence** (Business/Enterprise): LLM-powered analysis that:
- Suggests assignee and label for new triage issues
- Identifies potential duplicates
- Surfaces related issues

**Triage Rules** (Business/Enterprise): Custom automations that execute based on filterable properties, updating team, status, assignee, labels, project, and priority. Rules process sequentially top-to-bottom.

**Triage Responsibility**: Designate team members for rotation. Integrates with PagerDuty, OpsGenie, Rootly, Incident.io.

### Automations

Built-in:
- Auto-assign on state change
- Auto-close on PR merge
- Auto-label from templates
- Move to In Progress when git branch name copied
- Cycle automations (backlog ↔ cycle)

Webhook events for: Issues, Comments, Attachments, Documents, Emoji reactions, Projects, Project updates, Cycles, Labels, Users, Issue SLAs.

### GraphQL API

Full GraphQL API with introspection. Schema is 42,106 lines. SDK available at `@linear/sdk`.

---

## 4. Notion's Database Model

### Core Architecture

Every database item is a full Notion page. All views reference the same underlying data — changes in one view reflect in all others.

### Property Types for Task Management

- **Status** — kanban states
- **Date** — due dates, date ranges
- **Person** — assignment
- **Select / Multi-select** — labels, categories
- **Number** — estimates, points
- **Relation** — links to other databases
- **Rollup** — aggregations across relations
- **Formula** — computed values
- **Checkbox** — boolean flags

### Relations: The Relational Engine

**One-way relations** (default): Connection flows in one direction. Changes in one database don't sync to the other.

**Two-way relations**: Bidirectional sync. Adding a customer to Items shows up in the Customers database automatically.

**Self-referential relations**: Items in the same database relate to each other (e.g., parent task → subtask).

This enables the hierarchy:
```
Goals DB ←→ Projects DB ←→ Tasks DB ←→ Subtasks (self-referential)
```

### Rollups: Aggregation Across Relations

Three components:
1. Choose which relation to traverse
2. Select the property from related pages to aggregate
3. Apply calculation

Available calculations:
- **Count**: count all, count values, count unique, count empty/not empty, percent empty/not empty
- **Numeric**: sum, average, median, min, max, range
- **Date**: earliest, latest, date range
- **Display**: show original, show unique values

Example: Roll up task "Hours" through Project relation, apply Sum → project total hours.

**Limitation**: Cannot rollup a rollup (prevents circular dependencies).

### Formula Properties

Formulas 2.0 can:
- Reference properties of related items (reach into linked databases)
- Access member emails and names
- Calculate status percentages: `round(prop("Tasks Completed") / prop("Total Tasks") * 100)`
- Conditional logic for status rollup

### Views: Same Data, Different Lenses

Six view types on the same database:

| View | Best For | Groups By |
|------|----------|-----------|
| **Table** | Dense data, spreadsheet users | Any property |
| **Board** | Kanban workflow | Status, assignee, priority |
| **List** | Minimal, clean overview | Any property |
| **Calendar** | Date-driven planning | Date property |
| **Timeline** | Gantt-style project planning | Date range |
| **Gallery** | Visual cards | N/A |

Each view has independent:
- Filters (show/hide based on property values)
- Sorts (order by any property)
- Groups (cluster by any property)
- Visible/hidden properties
- Layout density

### Template Databases

Database templates pre-fill properties for recurring items. When creating a new page, select a template to auto-populate fields, relations, and even sub-pages.

---

## 5. TICK.md Protocol

### File Format

Single `TICK.md` file per project with YAML frontmatter and structured Markdown:

```yaml
---
project: my-project
schema_version: "1.0"
default_workflow: [backlog, todo, in_progress, review, done]
---
```

Task-level YAML metadata:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique task identifier |
| `status` | enum | Current workflow state |
| `priority` | string | Task urgency |
| `claimed_by` | string | Agent currently assigned |
| `created_by` | string | Task originator |
| `updated_at` | ISO 8601 | Last modification timestamp |
| `depends_on` | string[] | Array of blocking task IDs |
| `blocks` | string[] | Array of dependent task IDs |
| `history` | entry[] | Append-only action log |

### Coordination Protocol: Claim → Execute → Release

1. Agent runs `tick next` to find available work
2. Agent runs `tick claim <task-id>` — file locks, Git records the claim
3. Agent adds progress notes via `tick comment`
4. Agent marks complete with `tick done` — auto-unblocks dependent tasks
5. `tick sync` synchronizes with Git

### File Locking

When an agent claims a task, the file is locked to prevent concurrent edits. This eliminates merge conflicts from simultaneous agent operations.

### Dependency Resolution

Tasks reference other tasks via `depends_on` arrays. When a blocker completes (`tick done`), dependent tasks automatically unblock and become available via `tick next`.

### MCP Integration

MCP server allows AI agents to connect through the Model Context Protocol. Compatible with Claude, OpenClaw, and other MCP-compatible platforms.

### CLI Commands

- `tick next` — identify available work
- `tick claim` — assign task to agent
- `tick comment` — add timestamped notes
- `tick done` — mark task complete
- `tick sync` — synchronize with Git

### Git as Backend

No database, no server. Git handles:
- Sync between agents
- Audit trails (every change is a commit)
- Conflict resolution
- Version history

### Known Limitations (from production use)

Issues found in v1.2.0:
- Race conditions in simultaneous task claims
- Circular dependency edge cases
- Cross-platform file locking behavior differences
- YAML frontmatter parsing with special characters
- Scalability constraints for "large-scale agent swarms"
- No web dashboard yet (in development)

---

## 6. Agent Task Queue Patterns

### Queue Strategies

| Strategy | When to Use | Tradeoff |
|----------|-------------|----------|
| **FIFO** | Fair ordering, no urgency differentiation | Simple but no priority awareness |
| **Priority Queue** | Urgency-aware processing | Critical tasks processed first, risk of starvation |
| **Weighted Scoring** | Multi-factor ranking (urgency + age + importance) | Most flexible, most complex |
| **Deadline-based** | Time-sensitive work | Earliest deadline first, may starve low-priority |

### How Agents Decide What to Work On

**Priority-Based Scheduling**: Assign urgency levels to every task. Priority queues ensure critical messages are processed first.

**Orchestrator-Worker Pattern**: Central orchestrator assigns tasks to workers, managing execution and ensuring efficient delegation.

**Self-Selection Pattern**: Agents pull from shared queue. Used by TICK.md (`tick next`).

**Weighted Scoring Example**:
```
score = (priority_weight * priority) + (age_weight * hours_waiting) + (deadline_weight * 1/hours_until_due)
```

### Progress Reporting Patterns

**Streaming Updates**: Agent emits progress events as they work (Manus's `message_notify_user`).

**Completion Reports**: Agent works silently, reports only on finish.

**Checkpoint Model**: Agent reports at predefined milestones (every N steps, every phase completion).

**Hybrid**: Use non-blocking updates for long tasks, completion reports for short ones. Manus uses `notify` (non-blocking) vs `ask` (blocking).

### Preventing Duplicate Work

**Pessimistic Locking**: Lock the task before starting. Good for short tasks, high-conflict environments.
```
1. Agent requests lock on task
2. If locked by another agent → skip, find next task
3. If available → lock, execute, release
```

**Optimistic Locking**: Work on a copy, check version before writing back. Good for long tasks, low-conflict environments.
```
1. Agent reads task with version number
2. Agent works on task
3. Agent attempts to write back with version check
4. If version mismatch → retry or abort
```

**Namespace Isolation**: Create namespaces per agent role (planner, executor, verifier). Agents can only write to their namespace.

**TTL on Claims**: Add time-to-live to task claims. If agent dies, the claim expires and the task becomes available again.

**Authoritative Single Source**: One shared memory/database that every agent reads but only the responsible agent can write to. Prevents information divergence.

### Multi-Agent Coordination Patterns

**Centralized Orchestrator**: Master assigns tasks, workers execute. If a worker dies, master re-assigns.

**Decentralized (Peer)**: Agents coordinate using shared flags or atomic operations in shared memory.

**Hierarchical**: Supervisor agents manage teams of worker agents. Supervisors coordinate with each other.

**Event-Driven**: Agents react to events on shared bus. No central coordinator. Confluent/Kafka patterns.

---

## 7. Auto-Capture from External Sources

### Email → Task Detection

**Superhuman's approach** (October 2025 AI update):
- **Auto Labels**: Classify incoming emails into: response needed, waiting on, meetings, marketing, cold pitches
- **Auto Drafts**: Automatically writes follow-up emails in your voice
- **Auto Archive**: Archives marketing and cold emails
- **AI to-dos**: Auto-generated from emails and calendar events
- **Intent detection**: NLP reads content, detects sentiment and urgency, spots negative tone

**General NLP patterns for task detection**:
- Detect imperative phrases: "Please send," "Can you," "Need this done by"
- Extract deadlines: "by Friday," "next week," "before the meeting"
- Identify assignees: mentions, reply-to addresses
- Confidence scoring: high-confidence auto-creates, low-confidence asks for confirmation

### Slack → Task Creation

**Deemerge pattern**:
1. NLP identifies task language in messages
2. Extracts: action item, assignee, deadline, context
3. Asks for confirmation if confidence is low
4. Creates task in connected task manager (Linear, Asana, etc.)

**Standard Slack integration flow**:
1. User reacts with emoji (e.g., `:ticket:`) or uses slash command
2. Webhook fires to task manager API
3. Task created with link back to original Slack message
4. Status updates posted back to thread

### WhatsApp → Task Extraction

NLP agents detect when a WhatsApp chat contains a task:
- "send proposal by Friday" → Task: Send proposal, Due: Friday
- "follow up next week" → Task: Follow up, Due: next Monday
- Pulls contact info, due dates, action items from chat context

### Calendar → Task Generation

**Pre-meeting preparation**:
- Calendar event detected with attendees
- Auto-create: "Review agenda for [meeting]" task
- Auto-create: "Prepare materials for [attendee context]" task
- Pull relevant documents from knowledge base

**Post-meeting action items**:
- Meeting notes parsed for action items
- Tasks created with assignees from attendee list
- Due dates inferred from discussion context
- Link back to meeting recording/notes

### Cross-Platform Architecture

```
Source (Email/Slack/WhatsApp/Calendar)
    ↓
Ingestion Layer (webhooks, polling, IMAP)
    ↓
NLP Classification (intent detection, entity extraction)
    ↓
Confidence Scoring
    ↓
├── High confidence → Auto-create task
├── Medium confidence → Ask for confirmation
└── Low confidence → Log for review
    ↓
Task Manager (Linear, Notion, internal system)
    ↓
Feedback Loop (user confirms/rejects → improves model)
```

---

## 8. Knowledge System Integration

### How Tasks Link to Knowledge Bases

**Notion's approach**: Relation properties directly connect task pages to knowledge pages. A task can link to a wiki page, a spec document, or a research note. Rollups aggregate related knowledge (e.g., count of related docs, latest update date).

**Linear's approach**: Attachments and links on issues. Documents (Linear Docs) can be linked to projects. No native knowledge graph.

**Confluence/Jira pattern**: Bidirectional links between Jira issues and Confluence pages. Page mentions auto-create backlinks.

### Automatic Context Attachment

**Manus's approach**: Knowledge module provides domain-specific reference via RAG. Agent fetches external documents and APIs on demand. Priority: `datasource API > web search > model's internal knowledge`.

**Claude Code's approach**: CLAUDE.md files provide static project context. Sub-agents explore codebase on demand. Git integration provides change context.

### RAG Patterns for Task Context Enrichment

**Standard RAG flow for tasks**:
```
1. Task created with title + description
2. Embed task text → vector
3. Search knowledge base for similar vectors
4. Attach top-k results as context
5. Agent receives task + enriched context
```

**Agentic RAG (advanced)**:
- LLM acts as agent deciding when to retrieve, which tools to use
- Autonomous determination of whether output needs refinement
- Multiple retrieval rounds with query reformulation

### Mem0's Graph Memory Architecture

Dual-storage system:
1. **Entity Detection**: Extraction LLM identifies entities, relationships, and timestamps from conversation
2. **Dual Storage**: Embeddings → vector database; nodes and edges → graph backend (Neo4j, Memgraph, etc.)
3. **Hybrid Retrieval**: Vector similarity narrows candidates, graph returns related entities in a `relations` array

Data model:
- **Nodes**: Entity instances (people, organizations, places, projects)
- **Edges**: Directed relationships with temporal metadata
- **Context**: Timestamps and extraction confidence scores for pruning

Multi-agent support via `user_id`, `agent_id`, and `run_id` parameters — specialized agents maintain distinct knowledge while accessing shared user facts.

Key design decision: **Graph enriches vector search, doesn't replace it.** Vector search ordering is preserved; graph edges add supplementary context but don't reorder results.

### Mem.ai's Approach

AI-first note-taking with automatic organization:
- Bidirectional linking concepts from Roam Research
- AI permeates every aspect: capture → retrieval → discovery
- Emphasizes automated knowledge organization over manual graph construction
- End-to-end encryption

### Reflect's Approach

Networked thought with explicit linking:
- Manual bidirectional links between notes
- Daily notes as capture surface
- Graph visualization of connections
- Focus on personal knowledge management

### Integration Pattern for AOS

Recommended architecture for connecting tasks to knowledge:

```
Task Created
    ↓
Embed task title + description
    ↓
Vector search against vault (QMD)
    ↓
Graph traversal for related entities (if available)
    ↓
Attach top-k context snippets to task metadata
    ↓
Agent receives task with enriched context
    ↓
On task completion:
  - Extract new knowledge from task output
  - Update vault with findings
  - Create/update entity relationships
```

---

## Key Takeaways for AOS Task System Design

1. **Filesystem as memory** (Manus pattern): Write everything important to disk. Context windows are volatile; files are permanent.

2. **Single in-progress constraint** (Claude Code): Exactly one task active at any time prevents context thrashing.

3. **Attention recitation** (Manus todo.md): Rewriting objectives at the end of context combats goal drift in long sessions.

4. **Fixed state categories, custom names** (Linear): Categories (backlog/unstarted/started/completed/canceled) are fixed; team-specific names within each category provide flexibility without chaos.

5. **Claim-execute-release with TTL** (TICK.md): Simple coordination protocol that handles agent failures gracefully.

6. **Priority as numeric value** (Linear): 0-4 scale enables sorting and filtering. Names are display labels.

7. **Relations, not nesting** (Notion): Flat databases with typed relations are more flexible than deep hierarchies.

8. **Dual-form task descriptions** (Claude Code): Imperative ("Run tests") + active form ("Running tests") for different display contexts.

9. **Confidence-gated auto-capture**: High confidence → auto-create. Medium → confirm. Low → log for review.

10. **Hybrid retrieval** (Mem0): Vector search for relevance + graph traversal for relationships. Neither alone is sufficient.

---

## Sources

### Manus Architecture
- [Manus Technical Investigation (GitHub Gist)](https://gist.github.com/renschni/4fbc70b31bad8dd57f3370239dccd58f)
- [Manus Leaked System Prompt](https://github.com/jujumilk3/leaked-system-prompts/blob/main/manus_20250309.md)
- [Manus Tools and Prompts (GitHub Gist)](https://gist.github.com/jlia0/db0a9695b3ca7609c9b1a08dcbf872c9)
- [Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [Planning-with-Files Skill (Manus-style)](https://github.com/OthmanAdi/planning-with-files)
- [OpenManus Technical Analysis](https://llmmultiagents.com/en/blogs/OpenManus_Technical_Analysis)
- [System Prompts Collection](https://github.com/x1xhlol/system-prompts-and-models-of-ai-tools)

### Claude Code
- [Claude Code Internal Tools Implementation](https://gist.github.com/bgauryy/0cdb9aa337d01ae5bd0c803943aa36bd)
- [TodoWrite Tool Description](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-todowrite.md)
- [Under the Hood of Claude Code (Pierce Freeman)](https://pierce.dev/notes/under-the-hood-of-claude-code)
- [Claude Code Execution and Prompts Analysis](https://weaxsey.org/en/articles/2025-10-12/)
- [Tasks API vs TodoWrite](https://deepwiki.com/FlorianBruniaux/claude-code-ultimate-guide/8.1-session-management-commands)

### Linear
- [Linear Conceptual Model](https://linear.app/docs/conceptual-model)
- [Linear Issue Status / Workflows](https://linear.app/docs/configuring-workflows)
- [Linear Triage](https://linear.app/docs/triage)
- [Linear Priority](https://linear.app/docs/priority)
- [Linear GraphQL Schema](https://github.com/linear/linear/blob/master/packages/sdk/src/schema.graphql)
- [How We Built Triage Intelligence](https://linear.app/now/how-we-built-triage-intelligence)

### Notion
- [Notion Relations and Rollups](https://www.notion.com/help/relations-and-rollups)
- [Notion Database Views](https://www.notion.com/help/guides/using-database-views)
- [Notion Board View](https://www.notion.com/help/boards)

### TICK.md
- [TICK.md Official Site](https://www.tick.md/)
- [TICK.md Multi-Agent Coordination (Purple Horizons)](https://purplehorizons.io/blog/tick-md-multi-agent-coordination-markdown)

### Agent Patterns
- [AI Agent Orchestration Patterns (Microsoft Azure)](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
- [Choose a Design Pattern for Agentic AI (Google Cloud)](https://docs.google.com/architecture/choose-design-pattern-agentic-ai-system)
- [Multi-Agent Coordination Strategies (Galileo AI)](https://galileo.ai/blog/multi-agent-coordination-strategies)
- [Event-Driven Multi-Agent Systems (Confluent)](https://www.confluent.io/blog/event-driven-multi-agent-systems/)

### Knowledge Systems
- [Mem0 Graph Memory](https://docs.mem0.ai/open-source/features/graph-memory)
- [Context Engineering Guide (Mem0)](https://mem0.ai/blog/context-engineering-ai-agents-guide)

### Auto-Capture
- [Turn Slack Messages into Tasks (Deemerge)](https://www.deemerge.ai/post/turn-slack-messages-into-tasks-automatically)
- [Auto-Create Tasks from WhatsApp (Archiz)](https://archizsolutions.com/auto-create-tasks-from-whatsapp/)
- [Superhuman AI Updates](https://new.superhuman.com/)

# Qareen Visual Audit Report

**Date:** 2026-03-31
**Viewport:** 1440x900 (rendered at 960x507 device pixels)
**URL:** http://localhost:5173
**Auditor:** Claude Opus 4.6

---

## Global Issues

### G1. Dev server instability
The Vite dev server crashed twice during the audit. Navigating between pages (especially `/config` and `/tasks`) caused the process to exit entirely. This suggests either a fatal runtime error in a page component or a memory issue.

### G2. Header bar icon contrast
The top-right header icons (Search, Toggle theme, Refresh) are extremely low contrast against the dark background. At the audited viewport they are barely visible. The "Live" status indicator is the only element with sufficient contrast in that region.

### G3. Sidebar does not scroll in view on initial load
The sidebar contains sections FOCUS, KNOWLEDGE, AGENTS, SYSTEM, and MORE. At 900px window height, the MORE section (Analytics, People, Config, Channels) is below the fold and requires scrolling the sidebar. There is no visual indicator that more items exist below.

### G4. No page title heading on Companion
The header bar shows "Companion" in the top center, but the app logo/name "Qareen" is only visible in the browser tab title, not prominently in the header. The hamburger icon + page title pattern is fine, but the app branding is weak.

### G5. Warm theme compliance
The background is a very dark brown/charcoal, which is acceptable. However, several UI elements lean cold/neutral rather than warm:
- Card backgrounds on Agents, Projects, Tasks pages use a cool gray (#2a2a2a range) rather than a warm brown tint.
- The sidebar hover/active state uses a muted highlight that could be warmer.
- Empty state text uses a cold gray rather than a warm muted tone.

---

## Page-by-Page Issues

### 1. Companion (/)

**C1. Three-dot indicator is cryptic**
Three small dots appear at the top center of the chat area (approximately x:415, y:65). They have very low contrast (dark gray on dark background) and their purpose is unclear -- possibly a loading indicator or typing indicator, but with no label or animation context.

**C2. Chat area is mostly empty white space**
The center panel shows "Type a command, task, or thought below..." as a placeholder in the middle of the chat area. This text is low contrast (gray on dark). The empty state could be more inviting -- perhaps show recent activity, suggestions, or a greeting.

**C3. Input bar positioning**
The input bar with mic button, text field, and Send button is positioned near the bottom but there is a dark bar below it that appears to be dead space (approximately 20px of unused area at the very bottom of the viewport).

**C4. Queue panel -- "No pending decisions" shown even when items exist**
On initial load, the Queue panel on the right shows "No pending decisions" with an envelope icon. After the WebSocket connection delivers data, a real approval card appears. The initial empty state flashes before the real data arrives, creating a jarring transition.

**C5. Queue card text layout**
The approval card shows:
- "New Task" badge (orange, good contrast)
- "Create task: review the pricing proposal" -- title is readable
- "review the pricing proposal" -- subtitle duplicates part of the title
- "nuchay" tag followed by "Normal" priority with green dot
- Description text: "Add 'review the pricing proposal' to nuchay"
- Quoted text: "I need to review the pricing proposal for Nuchay"
The title and description are redundant. The card is information-dense but the hierarchy is unclear.

**C6. Queue keyboard shortcuts bar**
At the very bottom of the Queue panel, keyboard shortcuts are shown: "A Approve  E Edit  D Dismiss  Tab Next". These are very small and low contrast. The bar is partially clipped at the bottom of the viewport.

---

### 2. Tasks (/tasks)

**T1. Page heading "Tasks" inconsistency**
In Board view, the heading "Tasks" appears at the top. In List view, it disappears entirely. The Board/List toggle and "+ New" button shift position between views.

**T2. Board view -- columns do not scroll independently**
The TODO column (65 items) extends well below the viewport. The ACTIVE (3 items) and WAITING (0 items) columns are short. When scrolling down to see more TODO items, the ACTIVE and WAITING columns scroll out of view entirely. Each column should scroll independently within a fixed-height container.

**T3. Board view -- WAITING column empty state**
The WAITING column shows "No tasks" as plain text. This is adequate but could use a subtle icon or more styled empty state consistent with other pages.

**T4. Task card priority indicators**
Task cards show colored dots next to the project name ("aos"):
- Gray dot: appears for most TODO tasks
- Orange dot: appears for some tasks (e.g., "eventd LaunchAgent template")
There is no legend or tooltip explaining what the dot colors mean. The priority is not otherwise visible in Board view.

**T5. List view -- severe title truncation**
In List view, task titles are truncated with "..." after approximately 30 characters. Examples:
- "Phase 6: Mission Control integra..."
- "Phase 5: Recurring tasks + sche..."
- "Phase 4: Agent-to-agent deleg..."
The TITLE column is too narrow relative to the available space. The STATUS, PRI, PROJECT, and CREATED columns take up disproportionate width.

**T6. List view -- no task count visible**
Board view shows "TODO 65" and "ACTIVE 3" counts. List view shows no total count of displayed tasks.

**T7. Task card ID formatting**
Task IDs like "aos#3d2ee7" appear to be truncated hashes rather than human-readable IDs. Other cards show proper IDs like "aos#85.7", "aos#73". The inconsistency suggests some IDs are being rendered differently.

---

### 3. Projects (/projects)

**P1. "Unified Communication Layer" progress bar misleading**
The second project card shows "0/0" with a thin green progress bar. A green bar implies completion/health, but with zero tasks it should either show no bar or a neutral/gray bar. The stats "0 tasks  0 active  0 done" confirm this is an empty project.

**P2. No "Create Project" action**
Unlike the Tasks page which has a "+ New" button, the Projects page has no visible way to create a new project from the UI.

**P3. Category label styling**
Both projects show "aos-infrastructure" as a category label. The monospace font and muted color are fine, but there is no visual grouping if there were multiple categories.

**P4. Large empty space**
Only 2 project cards occupy the top portion of the page. The rest of the viewport is empty dark space with no empty state message or call to action.

---

### 4. Calendar (/calendar)

**CA1. Empty state is bland**
The empty state shows a calendar icon, "No schedule configured", and "Configure your daily schedule blocks in Settings to see them here." The icon and text are centered but very low contrast (gray on dark). The word "Settings" is not a clickable link, so the user has no clear path to configure their schedule.

**CA2. No calendar grid**
Despite being called "Calendar", the page shows only "TODAY'S SCHEDULE" with a single day view. There is no week/month grid, no date picker, and no way to navigate to other days.

**CA3. Date display**
"Tuesday, March 31, 2026" is displayed correctly but in a muted gray that has low contrast against the background.

**CA4. "TODAY'S SCHEDULE" label styling**
The label uses an uppercase, spaced style with a clock icon. The spacing between the icon and text is good, but the overall section feels sparse.

---

### 5. Vault (/vault)

**V1. Missing file browser**
The page says "Browse and search your knowledge base. Select a file from the sidebar." but there is NO file tree or file browser visible anywhere. The left sidebar only contains the global navigation links. The vault should have its own inner sidebar or panel showing the vault directory structure.

**V2. Duplicate search inputs**
The DOM contains two search textboxes with identical placeholder "Search vault...". Only one is visible in the UI (the top search bar in the left panel area). The second is hidden or overlapping. This is a code issue.

**V3. Search bar positioning**
The search bar appears in a narrow left column area (approximately 260px wide). The right 2/3 of the page is consumed by the empty state message with the book icon. The layout suggests a two-panel design (file tree left, content right) but the file tree panel is empty.

**V4. Empty state icon**
The book/document icon is appropriate but rendered in a very muted gray. The heading "Vault" below it is white (good contrast) but the description text is low contrast gray.

---

### 6. Memory (/memory)

**M1. CRITICAL: Route does not exist**
Navigating to `/memory` redirects to `/` (Companion page). The Memory sidebar link exists and is clickable, but the route is not implemented. The sidebar does not highlight "Memory" -- it highlights "Companion" after the redirect. This is a broken feature.

---

### 7. Agents (/agents)

**A1. Duplicate "Chief" agent cards**
Two identical "Chief" cards appear side by side:
- Left card: "Chief" with green "System" badge, description "the AOS orchestrator. Receives all requests, delegates to Steward and Advisor,...", tags "sonnet" and "all tools"
- Right card: "Chief" with NO "System" badge, but otherwise identical description and tags
This is a data deduplication issue -- likely two agent definition files for Chief are being loaded.

**A2. Missing page heading**
The page shows only the subtitle "Active agents and their trust configurations." without a prominent "Agents" heading. The header bar says "Agents" but the page content area lacks its own heading, unlike Tasks, Projects, Calendar, etc.

**A3. "Onboard" agent has no System badge**
The Onboard agent card lacks a "System" badge, unlike Chief and Steward which have green "System" badges. If Onboard is a system agent, it should have the badge.

**A4. Agent description truncation**
Agent descriptions are truncated with "..." in the cards:
- "system health, self-correction, and maintenance. Monitors services, detects..."
- "runs after fresh install to walk the operator through personalizing AOS. Conversation..."
The truncation is fine for a card view, but there is no way to expand or click through to see full details.

**A5. Model tag inconsistency**
- Chief: "sonnet" + "all tools"
- Steward: "haiku"
- Onboard: "sonnet"
- Engineer: "sonnet"
- Advisor: "sonnet"
The tags use different styling from the "System" badge (gray vs. green background). This is acceptable but the visual hierarchy between badge types could be clearer.

**A6. Card background is white/light**
Agent cards use a light/white background which creates high contrast against the dark page background. While readable, this deviates from the warm dark theme. Cards could use a dark card style with light text, consistent with task cards on the Tasks board view.

---

### 8. Approvals (/approvals)

**AP1. Error banner**
A red error banner displays: "Failed to load approval queue." with a "Retry" button. This indicates the backend approval API is not responding. The error is appropriately styled (red background, warning icon).

**AP2. Inconsistent heading**
The header bar says "Approvals" but the page heading says "Approval Queue". Pick one name.

**AP3. Tab bar with zero counts**
Four tabs are shown: "All", "Pending (0)", "Approved (0)", "Dismissed (0)". The zero counts are informative but the "All" tab lacks a count. Should show "All (0)" for consistency.

**AP4. Empty state below error**
Below the error banner and tabs, the page shows "No cards" with a shield icon and "Cards requiring your approval will appear here." This empty state is displayed simultaneously with the error banner, which is confusing -- if the load failed, the empty state should not be shown. It should show either the error OR the empty state, not both.

---

### 9. System (/system)

**S1. Missing disk and RAM values**
The system health indicators show:
- "Disk [bar] % GB free" -- the actual percentage and GB value are missing
- "RAM [bar] % GB" -- the actual percentage and GB value are missing
The progress bars are rendered (green) but the numeric labels show literal "%" and "GB" with no data. The API is not returning the metrics or the values are null/undefined.

**S2. Dashboard service shows red indicator**
The Dashboard service card has a red dot (indicating offline/error). Its button says "Start" instead of "Restart" like the other services. This is correct behavior (service is stopped) but the red dot has no label -- hovering does not explain the status.

**S3. Service card heights inconsistent**
The Bridge and Dashboard cards in the top row appear taller than the Eventd and Listen cards below them. This creates an uneven grid. All service cards should have consistent height.

**S4. Service description quality varies**
- Bridge: "Telegram + Slack messaging" (descriptive)
- Dashboard: "dashboard" (unhelpful, just repeats the name)
- Eventd: "eventd" (unhelpful)
- Listen: "Voice transcription pipeline" (descriptive)
- Whatsmeow: "whatsmeow" (unhelpful)
- Transcriber: "transcriber" (unhelpful)
Several descriptions just repeat the service name and provide no useful information.

**S5. Cron Jobs section is collapsed**
The "CRON JOBS" section shows a collapsed "Other" group with "23 jobs". There is no categorization or status information visible without expanding.

**S6. No service restart/action feedback**
Buttons for "Restart" and "Logs" are present on each service card but there is no indication of whether clicking them will provide feedback or confirmation.

---

### 10. Pipelines (/pipelines)

**PI1. Empty state is acceptable but minimal**
Shows a pipeline icon, "No pipelines defined", and "Workflow pipelines will appear here when configured." This is a clean empty state but lacks any action button or link to documentation on how to configure pipelines.

**PI2. No heading on page**
The page has a "Pipelines" heading at the top left which is good. The empty state is centered. Acceptable layout.

---

### 11. Analytics (/analytics)

**AN1. Goal progress bars show 0%**
Both goals ("Ship AOS v2 as a packageable system" and "Test Goal") show a progress bar at 0% with the label "0%". The progress bars are very thin and dark, making them hard to distinguish from the card background.

**AN2. Checkbox alignment**
Each goal card has a checkbox on the right side. The checkbox appears to be a square outline with no fill. Its purpose is unclear -- does checking it mark the goal as complete? There is no tooltip or label.

**AN3. Metrics section empty state**
The "METRICS" section shows "No metrics tracked yet" with a bar chart icon and description "Metrics will appear here as KPIs are defined and data flows in." This is fine but like other empty states, lacks a call to action.

**AN4. No "Add Goal" action**
There is no visible button to create a new goal from this page.

---

### 12. People (/people)

**PE1. Non-person entries in contacts list**
"14th Street Pizza" appears as a contact entry. The people/contacts list should filter out businesses or at minimum categorize them differently.

**PE2. Malformed contact entry**
"A -" appears as a contact name. This is likely a data quality issue but the UI should handle edge cases (empty/partial names) more gracefully.

**PE3. All contacts show "never" for last interaction**
Every visible contact shows "never" in the right column. If the last-interaction data is not being tracked, this column should either be hidden or show a dash/empty instead of "never" for every row.

**PE4. Avatar initials inconsistency**
- "14th Street Pizza" shows "1S" (first char of each word)
- "A -" shows "A -" (includes the dash character in the avatar)
- "Aadil Farooq" shows "AF" (correct)
The avatar initial generation needs better handling of special characters and numbers.

**PE5. Surfaces tab is sparse**
Clicking "Surfaces" shows "No surfaces right now." with no explanation of what a "surface" is. No icon, no description, just plain text.

**PE6. Connection status flickered to "Offline"**
While on the People page, the header status changed from "Live" (green dot) to "Offline" (gray dot). This suggests WebSocket instability on this page.

---

### 13. Config (/config)

**CF1. Profile tab is minimal**
Shows only "Name: Hisham" and "Timezone: America/Chicago" with an "Edit" link. The layout is clean but very sparse -- the right side has all the values right-aligned, which creates a large gap between the labels and values.

**CF2. Schedule tab shows "Not set" for all items**
Morning briefing, Evening checkin, and Quiet hours all show "Not set". The items are not clickable and there is no "Edit" or "Set" action visible.

**CF3. Trust tab is minimal**
Shows only "Default trust level: Level 3" with no explanation of what trust levels mean or how to change them.

**CF4. Accounts tab -- naming inconsistency**
Account names use inconsistent casing:
- "Google" (capitalized)
- "Telegram" (capitalized)
- "Github" (should be "GitHub")
- "Apple_dev" (snake_case with lowercase)
- "Paypal" (should be "PayPal")
- "Openrouter" (should be "OpenRouter")
All show "configured" status in monospace font. No way to see details or edit.

**CF5. Integrations tab -- all inactive**
Three integrations (apple_native, google_suite, telegram) all show "Inactive" status. The names use snake_case which is developer-facing, not user-friendly. Should be "Apple Native", "Google Suite", "Telegram".

**CF6. Config page previously caused crash**
On first attempt to navigate to `/config`, the dev server crashed completely. This was reproduced and suggests the Config component has a problematic initialization path.

---

### 14. Channels (/channels)

**CH1. Missing page heading**
The page has no "Channels" heading. It jumps directly into the channel cards. Other pages have consistent headings ("Tasks", "Projects", "System Health", etc.).

**CH2. Minimal channel information**
Each channel card shows:
- Icon (paper plane for Telegram, chat bubble for WhatsApp)
- Name + "Connected" status with green dot
- "active" badge (green)
- An em-dash button on the right (likely a menu or actions)
The cards are very minimal. No stats, no message counts, no connection details.

**CH3. Em-dash button is cryptic**
The rightmost element on each channel row appears to be an em-dash character or horizontal line. Its function is unclear -- it might be a kebab menu, an expand toggle, or a status indicator. No hover state or tooltip was observed.

**CH4. Large empty space**
Only 2 channels are shown. The rest of the page is empty dark space.

---

## Summary of Critical Issues

| Priority | Issue | Page |
|----------|-------|------|
| Critical | `/memory` route is broken -- redirects to `/` | Memory |
| Critical | Dev server crashes when navigating between pages | Global |
| Critical | Disk/RAM values missing (show "% GB" with no numbers) | System |
| High | Duplicate "Chief" agent cards | Agents |
| High | Vault file browser is completely missing | Vault |
| High | Approval page shows error + empty state simultaneously | Approvals |
| High | Task title truncation in List view is severe | Tasks |
| Medium | Board view columns do not scroll independently | Tasks |
| Medium | Empty project shows misleading green progress bar | Projects |
| Medium | Calendar has no navigation, date picker, or week/month view | Calendar |
| Medium | Agent cards use white/light backgrounds (breaks dark theme) | Agents |
| Medium | Service descriptions are just the service name repeated | System |
| Medium | Account names have inconsistent casing | Config |
| Medium | Header icons have very low contrast | Global |
| Low | Three-dot indicator on Companion has no visible purpose | Companion |
| Low | Empty states lack calls to action across most pages | Multiple |
| Low | Contact avatars mishandle special characters | People |
| Low | Channels page has no heading | Channels |

---

## Design Language Compliance

| Criterion | Status | Notes |
|-----------|--------|-------|
| Warm dark browns (not cold blacks) | Partial | Background is warm-ish but card surfaces on Agents page are cold white |
| Pure white headings | Pass | Page headings are white and readable |
| Warm amber/gold accent | Pass | Send button, + New button, badges use amber/gold correctly |
| No cold/blue tints | Partial | Some empty state text and card backgrounds lean neutral/cold |
| Consistent spacing | Fail | Card heights vary on System page; List view column widths are unbalanced |
| Readable contrast | Partial | Headings are good; secondary text and header icons are too low contrast |

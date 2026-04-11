---
name: dissect
description: "Deep multi-page exploration of a website — walks the site as a user, maps navigation and user flows, captures page-by-page specs, extracts API patterns from network traffic, and documents state transitions. Trigger on: /dissect, 'study this site', 'map this product', 'how does this site work', 'reverse engineer this'. Provide URL and optionally a focus area."
argument-hint: "<url> [focus: area to explore deeply]"
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, Agent
---

# Dissect — Deep Website Exploration

Walk through **$ARGUMENTS** as a user, mapping every flow, page, and interaction into a comprehensive study folder.

This is experience capture — not just screenshots, but understanding how the product works through using it.

## Pre-Flight

1. Parse arguments — URL required, optional `focus:` directive
2. Create study output directory: `vault/knowledge/references/studies/<site-name>/`
3. Create subdirectories: `pages/`, `api/`
4. Initialize dev-browser session: `dev-browser --browser dissect-<site-name>`
5. Navigate to the target URL

## Phase 1: Initial Recon

### Landing Page Capture
1. Screenshot at 1440px and 390px
2. `page.snapshotForAI()` for DOM structure
3. Identify all navigation elements, CTAs, footer links
4. Build initial site map from visible navigation

### Site Map Discovery
```javascript
(function() {
  const links = new Set();
  const origin = location.origin;
  document.querySelectorAll('a[href]').forEach(a => {
    try {
      const url = new URL(a.href, origin);
      if (url.origin === origin) links.add(url.pathname);
    } catch(e) {}
  });
  // Also check nav, header, footer specifically
  const nav = document.querySelectorAll('nav a, header a, footer a, [class*="sidebar"] a');
  nav.forEach(a => {
    try {
      const url = new URL(a.href, location.origin);
      if (url.origin === origin) links.add(url.pathname);
    } catch(e) {}
  });
  return JSON.stringify([...links].sort(), null, 2);
})()
```

### Run Blueprint sub-task
Extract the design system (DESIGN.md) as a parallel sub-task — it provides context for everything else.

## Phase 2: Page-by-Page Exploration

For each page in the site map (prioritize by: main nav pages first, then secondary, skip legal/policy pages unless focused):

### Navigate
```bash
dev-browser --browser dissect-<site-name> <<'EOF'
const page = await browser.getPage("main");
await page.goto("<page-url>");
await page.waitForLoadState("networkidle");
console.log(JSON.stringify({ url: page.url(), title: await page.title() }));
EOF
```

### Snapshot
1. Screenshot at 1440px (desktop)
2. `page.snapshotForAI()` for structure
3. Note: page layout type (single column, sidebar, grid, split)

### Interaction Sweep
For each page, systematically test:

**Scroll sweep:**
- Scroll slowly top to bottom
- At each section: does anything animate in? Does the header change? Do elements stick?
- Record scroll-triggered behaviors with approximate trigger positions

**Click sweep:**
- Click every interactive element: tabs, buttons, dropdowns, accordions, cards
- Record what happens: content changes, modals open, navigation occurs
- For tabs/pills: click EACH one, record content per state

**Hover sweep:**
- Hover buttons, cards, links, images, nav items
- Record CSS changes: color, scale, shadow, underline, opacity

### Network Capture
During the interaction sweep, capture API calls:
- Use Chrome MCP `read_network_requests` with `urlPattern: '/api/'` if in interactive mode
- Or use dev-browser with page route interception:
```javascript
const requests = [];
await page.route('**/*', route => {
  const req = route.request();
  if (req.resourceType() === 'xhr' || req.resourceType() === 'fetch') {
    requests.push({ method: req.method(), url: req.url(), postData: req.postData() });
  }
  route.continue();
});
// ... perform interactions ...
console.log(JSON.stringify(requests, null, 2));
```

### Write Page Spec
Save to `studies/<site>/pages/<page-name>/spec.md`:
- Page URL, title, layout type
- Key components and their roles
- Interactive elements and their behaviors
- API calls made on this page
- Responsive notes

Save screenshots to the same directory.

## Phase 3: Flow Mapping

After exploring individual pages, identify and document user journeys:

### Primary Flows
- **Main CTA path**: What happens when a new visitor clicks the primary CTA?
- **Navigation flow**: How do users move between major sections?
- **Onboarding/signup**: If accessible, what are the steps?
- **Feature flows**: How does the core product feature work?

### For Each Flow
Document as a numbered sequence:

```markdown
## Flow: [Name]

### Step 1: [Page/State Name]
- **URL:** /path
- **Screenshot:** pages/step-1.png
- **What user sees:** [description]
- **Available actions:** [list of things user can do]

### Transition: Step 1 → Step 2
- **Trigger:** Click "Sign Up" button
- **Animation:** Slide-left page transition, 300ms ease
- **API call:** POST /api/auth/register { email, password }
- **Response:** { token, user: { id, name } }

### Step 2: [Page/State Name]
- **URL:** /onboarding
- **What changed:** New page with progress indicator, step 1 of 3
...
```

### Save FLOWS.md
Write to `studies/<site>/FLOWS.md` with all discovered flows.

## Phase 4: API Extraction

Compile all observed API calls from the exploration:

### Organize by Domain
Group endpoints by resource type (users, projects, content, auth, etc.)

### For Each Endpoint
- Method + path
- Request headers (note auth patterns: Bearer, cookies, API keys)
- Request body (if POST/PUT/PATCH)
- Response body (sample)
- When it's called (which page, which interaction)

### Generate Types
If `quicktype` is available:
```bash
echo '<collected-json-responses>' | quicktype -l typescript -o studies/<site>/api/types.ts
```
Otherwise, manually infer TypeScript interfaces from response shapes.

### Auth Flow Documentation
- How does the site authenticate? (JWT, session cookies, OAuth, API keys)
- Where are tokens stored? (localStorage, cookies, headers)
- What claims does the JWT contain? (decode with base64 if captured)
- What's the refresh pattern?

### Write API.md
Human-readable API reference at `studies/<site>/API.md`:
- Organized by domain, not raw endpoint list
- Each section explains what the API does, not just its shape
- Include example request/response pairs
- Note auth requirements

Optionally write `studies/<site>/api/openapi.yaml` if enough data captured.

## Phase 5: Synthesis

### SUMMARY.md (Non-Technical)
Write at `studies/<site>/SUMMARY.md`:
- What the product does (one paragraph)
- How the UI is organized (with screenshots)
- Key user flows in plain language
- What's notable about the design/implementation
- Tech stack observations (framework hints from HTML/headers)
- What we'd need to build something similar

This should be readable by a non-technical client. No jargon. Use screenshots as illustrations.

### Organize Study Folder
Verify all files are in place:
```
studies/<site>/
├── DESIGN.md
├── FLOWS.md
├── API.md
├── SUMMARY.md
├── api/
│   ├── openapi.yaml (if generated)
│   └── types.ts (if generated)
└── pages/
    └── <page-name>/
        ├── spec.md
        ├── desktop.png
        ├── mobile.png
        └── states/ (if applicable)
```

## Focus Mode

When the operator specifies a focus area (e.g., `/dissect https://linear.app focus: issue creation`):

1. Still do initial recon (site map, landing page)
2. Navigate directly to the relevant area
3. Go deep on that specific flow — every state, every transition, every API call
4. Skip unrelated pages entirely
5. FLOWS.md focuses on the specified area
6. SUMMARY.md scoped to the focus area

## Guided Mode

The operator can guide the exploration interactively:
- "Go to the settings page" → navigate there, continue protocol
- "Click the upgrade button" → click, capture what happens
- "Focus on this section" → run harvest-style extraction on it
- "Skip the blog" → exclude from exploration

## Completion

Report:
- Pages explored (count + list)
- Flows documented (count + names)
- API endpoints discovered (count)
- Components identified (count)
- Total screenshots
- Study folder path
- Any areas not accessible (login walls, paywalls, broken pages)

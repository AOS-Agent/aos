# ~/project/ — how this directory works

Installed by AOS as `~/project/CLAUDE.md`. Auto-loaded in any session running
under `~/project/`, so it is deliberately short.

**This file is a pointer, not the enforcement.** The `project` CLI is the
sanctioned way to change this directory; prose cannot stop anything, and a
policy you can ignore by not reading it is not a policy. When in doubt, run the
command — it already knows these rules.

## Zones

```
~/project/
  <project>/     active work — git, and a .aos/project.yaml manifest
  _ref/          third-party clones, kept to read
  _archive/      finished work
  _scratch/      ephemeral
```

Flat. No domain nesting — a project can belong to two domains and to neither,
and every nesting scheme eventually forces a directory to lie about itself.
Relationships are declared in manifests instead.

Location is a signal, not decoration: anything in `_archive/` is done, anything
in `_ref/` is somebody else's, and nothing in `_scratch/` is expected to
survive. Tools that never read a manifest still get those three facts right.

## The four commands

```bash
project new <name>       # create: directory, git, manifest, README, first commit
project adopt <dir>      # write a manifest for something that already exists
project list             # what is here, by zone, with drift called out
project archive <name>   # finish it: final commit, tag, marker, move to _archive/
```

Not on PATH? `~/aos/core/bin/cli/project`.

Do not `mkdir && git init` a new project by hand. It works, and it produces a
directory that cannot identify itself — which is the entire problem this layer
exists to fix.

## Naming

Kebab-case: `quran-garden`, not `Quran_Garden`. Names starting with `_` are
reserved for zones. Names ending in `-wt` are refused — that was the old
sibling-worktree convention and worktrees do not live there any more.

## Git tiers

Not everything belongs in git, and the difference is recorded on disk rather
than remembered.

| Tier | Rule |
|---|---|
| Your own projects | git + a **private** remote. Keep the tree clean — uncommitted work exists nowhere but this disk. |
| `_ref/` clones | Fetch only. Never commit, never push. They are re-clonable; treat them as read-only. |
| Heavy media, business archives, mirrored drives | **No git.** Write a `.aos/no-git` marker and back the directory up instead. |

The `.aos/no-git` marker is a deny, not a preference: `project new` and
`project adopt` refuse to `git init` a directory carrying one, `--git` included.
Some directories here are tens of gigabytes of other people's data, and a
well-meaning `git init` inside one is actively harmful.

If a directory has no version control **and** no marker, nothing records whether
that was a decision or an oversight — that silence is what `project list`
reports as drift.

## Worktrees

Branch checkouts live at `<project>/.claude/worktrees/<branch-slug>` — inside
the project, always. Never a sibling `<project>-wt/` directory, and never
anywhere under `/private/tmp`: macOS wipes files there on a timer while leaving
the directories standing, which has already destroyed 19 worktrees on this
machine.

## Datasets

A dataset lives **inside the project that owns it**. There is no shared data
zone; projects that consume it say so in their manifest:

```yaml
depends_on:
  - quran-garden-data
```

By id, never by path. A path breaks the moment the dependency is archived.

## What is derived, and never written here

Manifests declare; they never report. There is no `status`, `state`, `progress`
or `last_updated` field in `.aos/project.yaml` and the validator rejects them —
those are derived from tasks, git and sessions. Lifecycle facts that genuinely
belong on disk are markers (`.aos/archived`, `.aos/no-git`), not manifest keys.

For what is actually happening in a project, ask the tracker: `work projects`.

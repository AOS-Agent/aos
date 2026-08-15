# Project Structure

Applies in **any** working directory. The full policy lives in
`~/project/CLAUDE.md`, which loads automatically once you are inside
`~/project/` — read it before doing anything structural there. This is the part
you need before you get there.

## New projects go in ~/project/, via the CLI

```bash
project new <name>        # kebab-case; creates dir + git + manifest + first commit
```

Not `mkdir ~/project/thing && git init`. That works and produces a directory
that cannot identify itself, which is the problem the project layer exists to
fix. If `project` is not on PATH: `~/aos/core/bin/cli/project`.

Adopt something that already exists with `project adopt <dir>`; finish one with
`project archive <name>`. `project list` shows what is there and what has
drifted.

## Zones

```
~/project/<project>/   active work
~/project/_ref/        clones of other people's repos — fetch only, never commit
~/project/_archive/    finished
~/project/_scratch/    ephemeral
```

Clone a third-party repo into `_ref/`, not alongside the operator's own work.

## Never git-init a directory carrying .aos/no-git

That marker is a deny, deliberately placed. Some directories under `~/project/`
are tens of gigabytes of mirrored drives and business archives where version
control is actively harmful. Check for it before running `git init` anywhere
under `~/project/`; the `project` CLI already does.

## Worktrees

`<project>/.claude/worktrees/<branch-slug>` — inside the project. Never a
sibling `<project>-wt/`, and never under `/private/tmp` (macOS wipes it on a
timer; 19 worktrees have already been lost that way).

## Manifests declare, they never report

`.aos/project.yaml` holds identity and intent. It has no `status`, `state`, or
`progress` field and the validator rejects them — state is derived from tasks,
git and sessions. Cross-project dependencies go in `depends_on`, by project id,
never by path.

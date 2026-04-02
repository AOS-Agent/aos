"""
AOS Work Metrics — Flow metrics computed from work.yaml.

Provides: throughput, cycle time, lead time, WIP, capture sources, goal health.
All functions are pure — they take data in, return metrics out. No I/O.
"""

import os
from datetime import datetime, date, timedelta
from pathlib import Path
import yaml


def _parse_date(d) -> date | None:
    """Parse a date from various formats."""
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    try:
        # Handle "2026-03-21T10:38:17" and "2026-03-21"
        return datetime.fromisoformat(str(d).split("T")[0]).date()
    except (ValueError, TypeError):
        return None


def _days_between(start, end) -> float | None:
    """Days between two date-like values."""
    s = _parse_date(start)
    e = _parse_date(end)
    if s and e:
        return (e - s).total_seconds() / 86400
    return None


def compute_period_metrics(tasks: list, period_start: date, period_end: date) -> dict:
    """Compute flow metrics for a given period.

    Args:
        tasks: All tasks (including done/cancelled)
        period_start: Start of period (inclusive)
        period_end: End of period (inclusive)

    Returns dict with throughput, cycle_time, lead_time, wip, captures.
    """
    completed_in_period = []
    created_in_period = []
    active_during_period = []

    for t in tasks:
        created = _parse_date(t.get("created"))
        completed = _parse_date(t.get("completed"))
        started = _parse_date(t.get("started"))
        status = t.get("status", "")

        # Completed in this period
        if completed and period_start <= completed <= period_end:
            completed_in_period.append(t)

        # Created in this period
        if created and period_start <= created <= period_end:
            created_in_period.append(t)

        # Active during this period (was active at some point)
        if status == "active":
            active_during_period.append(t)
        elif completed and started:
            # Was active during period if started before end and completed after start
            if started <= period_end and completed >= period_start:
                active_during_period.append(t)

    # Throughput: tasks completed in period
    throughput = len(completed_in_period)

    # Cycle time: active → done (days)
    cycle_times = []
    for t in completed_in_period:
        ct = _days_between(t.get("started"), t.get("completed"))
        if ct is not None:
            cycle_times.append(ct)

    cycle_time_avg = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else None

    # Lead time: created → done (days)
    lead_times = []
    for t in completed_in_period:
        lt = _days_between(t.get("created"), t.get("completed"))
        if lt is not None:
            lead_times.append(lt)

    lead_time_avg = round(sum(lead_times) / len(lead_times), 1) if lead_times else None

    # WIP: tasks currently active
    wip_current = len([t for t in tasks if t.get("status") == "active"])

    # Capture sources
    sources = {}
    for t in created_in_period:
        src = t.get("source", "manual")
        sources[src] = sources.get(src, 0) + 1

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "throughput": throughput,
        "created": len(created_in_period),
        "cycle_time_avg_days": cycle_time_avg,
        "lead_time_avg_days": lead_time_avg,
        "wip_current": wip_current,
        "wip_period_max": len(active_during_period),
        "capture_sources": sources,
        "completed_tasks": [{"id": t["id"], "title": t["title"]} for t in completed_in_period],
    }


def compute_current_week(tasks: list) -> dict:
    """Compute metrics for the current week (Mon-Sun)."""
    today = date.today()
    # Monday of this week
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    metrics = compute_period_metrics(tasks, week_start, week_end)
    metrics["period"] = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
    return metrics


def compute_goal_health(goals: list, tasks: list) -> list:
    """Compute health status for each goal.

    A goal is healthy if:
    - It has tasks linked to it (directly or via project)
    - Those tasks are moving (active, recently completed)
    - Progress matches timeline (if timeframe is set)
    """
    health = []
    for g in goals:
        if g.get("status") != "active":
            continue

        goal_id = g["id"]

        # Find tasks linked to this goal (directly or via project)
        linked_tasks = [t for t in tasks if t.get("goal") == goal_id]
        # Also find tasks in projects that link to this goal
        # (projects link to goals, tasks link to projects)
        # We'd need project data for this — keep it simple for now
        linked_active = [t for t in linked_tasks if t.get("status") == "active"]
        linked_done = [t for t in linked_tasks if t.get("status") == "done"]

        # Time elapsed (if timeframe set)
        time_elapsed = None
        on_track = None
        if g.get("timeframe"):
            tf_start = _parse_date(g["timeframe"].get("start"))
            tf_end = _parse_date(g["timeframe"].get("end"))
            if tf_start and tf_end:
                total_days = (tf_end - tf_start).days
                elapsed_days = (date.today() - tf_start).days
                if total_days > 0:
                    time_elapsed = round(elapsed_days / total_days, 2)

        # Progress from key results (if any)
        progress = g.get("progress")
        if not progress and g.get("key_results"):
            krs = g["key_results"]
            kr_progress = []
            for kr in krs:
                target = kr.get("target", 0)
                current = kr.get("current", 0)
                if target > 0:
                    kr_progress.append(min(current / target, 1.0))
            if kr_progress:
                progress = round(sum(kr_progress) / len(kr_progress), 2)

        # Determine health
        if time_elapsed is not None and progress is not None:
            drift = round(time_elapsed - progress, 2)
            on_track = drift < 0.2  # Within 20% of expected
        elif linked_active or linked_done:
            on_track = True
            drift = None
        else:
            on_track = False
            drift = None

        entry = {
            "id": goal_id,
            "title": g["title"],
            "progress": progress,
            "time_elapsed": time_elapsed,
            "on_track": on_track,
            "drift": drift,
            "linked_tasks": len(linked_tasks),
            "active_tasks": len(linked_active),
            "completed_tasks": len(linked_done),
        }
        health.append(entry)

    return health


def save_weekly_snapshot(metrics: dict, metrics_dir: Path = None):
    """Save weekly metrics snapshot to disk."""
    if metrics_dir is None:
        metrics_dir = Path.home() / ".aos" / "work" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    period = metrics.get("period", date.today().isoformat())
    filepath = metrics_dir / f"{period}.yaml"

    # Add generation timestamp
    metrics["generated"] = datetime.now().isoformat(timespec="seconds")

    # Atomic write: temp file + rename prevents corruption on crash
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=str(metrics_dir), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(metrics, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(filepath))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return filepath


def compute_drift(goals: list, tasks: list) -> dict:
    """Compare goal weights vs actual task completion distribution.

    If a goal has weight 0.4 (40% of effort) but only 10% of completed tasks
    are linked to it, that's a drift of 0.30 — you're spending less time on it
    than you planned.

    Returns drift analysis with per-goal comparison.
    """
    # Only look at active goals with weights
    weighted_goals = [g for g in goals if g.get("status") == "active" and g.get("weight")]

    if not weighted_goals:
        return {
            "has_drift_data": False,
            "message": "No goals with weights set. Add weights to goals to enable drift detection.",
        }

    # Completed tasks (all time for now — could be scoped to a period)
    completed = [t for t in tasks if t.get("status") == "done"]
    total_completed = len(completed) if completed else 1  # avoid div by zero

    # Count completed tasks per goal (via project linkage)
    # Build project→goal mapping from project data
    goal_task_counts = {g["id"]: 0 for g in weighted_goals}
    unlinked = 0

    # Build project→goal lookup (projects have a 'goal' field)
    project_to_goal = {}
    try:
        _work_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'work'))
        sys.path.insert(0, _work_dir)
        import backend as _engine
        for p in _engine.get_all_projects():
            if p.get("goal"):
                project_to_goal[p["id"]] = p["goal"]
    except Exception:
        pass

    for t in completed:
        task_goal = t.get("goal")
        task_project = t.get("project")

        if task_goal and task_goal in goal_task_counts:
            goal_task_counts[task_goal] += 1
        elif task_project and task_project in project_to_goal:
            # Project links to a goal
            goal_id = project_to_goal[task_project]
            if goal_id in goal_task_counts:
                goal_task_counts[goal_id] += 1
            else:
                unlinked += 1
        else:
            unlinked += 1

    # Compute drift per goal
    drifts = []
    total_weight = sum(g.get("weight", 0) for g in weighted_goals)

    for g in weighted_goals:
        weight = g.get("weight", 0)
        normalized_weight = weight / total_weight if total_weight > 0 else 0
        actual_share = goal_task_counts[g["id"]] / total_completed
        drift_value = round(normalized_weight - actual_share, 2)

        direction = "under" if drift_value > 0.05 else "over" if drift_value < -0.05 else "aligned"

        drifts.append({
            "goal_id": g["id"],
            "goal_title": g["title"],
            "intended_weight": round(normalized_weight, 2),
            "actual_share": round(actual_share, 2),
            "drift": drift_value,
            "direction": direction,
            "tasks_completed": goal_task_counts[g["id"]],
        })

    # Sort by absolute drift (biggest misalignment first)
    drifts.sort(key=lambda d: abs(d["drift"]), reverse=True)

    max_drift = max(abs(d["drift"]) for d in drifts) if drifts else 0

    return {
        "has_drift_data": True,
        "total_completed": total_completed,
        "unlinked_tasks": unlinked,
        "max_drift": max_drift,
        "aligned": max_drift < 0.15,
        "goals": drifts,
    }


def format_drift_display(drift: dict) -> str:
    """Format drift analysis for display."""
    if not drift.get("has_drift_data"):
        return drift.get("message", "No drift data available.")

    lines = []
    lines.append("Drift Analysis")
    lines.append("=" * 40)

    status = "ALIGNED" if drift["aligned"] else "DRIFTING"
    lines.append(f"  Status: {status} (max drift: {drift['max_drift']})")
    lines.append(f"  Based on {drift['total_completed']} completed tasks")

    if drift["unlinked_tasks"] > 0:
        lines.append(f"  Unlinked tasks: {drift['unlinked_tasks']} (not tied to any goal)")

    lines.append("")
    for g in drift["goals"]:
        bar_intended = int(g["intended_weight"] * 20)
        bar_actual = int(g["actual_share"] * 20)
        marker = " !" if g["direction"] != "aligned" else ""

        lines.append(f"  {g['goal_title']}")
        lines.append(f"    Intended: {'|' * bar_intended}{'.' * (20 - bar_intended)} {int(g['intended_weight'] * 100)}%")
        lines.append(f"    Actual:   {'|' * bar_actual}{'.' * (20 - bar_actual)} {int(g['actual_share'] * 100)}%")
        lines.append(f"    → {g['direction']}{marker} ({g['tasks_completed']} tasks)")
        lines.append("")

    return "\n".join(lines)


def format_metrics_display(metrics: dict, goal_health: list = None) -> str:
    """Format metrics for human-readable display."""
    lines = []
    period = metrics.get("period", "current")
    lines.append(f"Work Metrics — {period}")
    lines.append("=" * 40)

    lines.append(f"  Throughput:    {metrics['throughput']} tasks completed")
    lines.append(f"  Created:      {metrics['created']} new tasks")

    if metrics["cycle_time_avg_days"] is not None:
        lines.append(f"  Cycle time:   {metrics['cycle_time_avg_days']}d avg (active→done)")
    else:
        lines.append(f"  Cycle time:   — (no completed tasks with start dates)")

    if metrics["lead_time_avg_days"] is not None:
        lines.append(f"  Lead time:    {metrics['lead_time_avg_days']}d avg (created→done)")

    lines.append(f"  WIP:          {metrics['wip_current']} active now")

    if metrics["capture_sources"]:
        sources = ", ".join(f"{k}: {v}" for k, v in sorted(metrics["capture_sources"].items()))
        lines.append(f"  Sources:      {sources}")

    if metrics["completed_tasks"]:
        lines.append("")
        lines.append("  Completed:")
        for t in metrics["completed_tasks"]:
            lines.append(f"    - {t['id']}: {t['title']}")

    if goal_health:
        lines.append("")
        lines.append("  Goal Health:")
        for g in goal_health:
            status = "on track" if g["on_track"] else "AT RISK"
            progress = f"{int(g['progress'] * 100)}%" if g["progress"] is not None else "?"
            time_info = f", {int(g['time_elapsed'] * 100)}% time elapsed" if g["time_elapsed"] is not None else ""
            lines.append(f"    {g['id']}: {g['title']} — {progress} ({status}{time_info})")

    return "\n".join(lines)

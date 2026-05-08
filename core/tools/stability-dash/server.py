#!/usr/bin/env python3
"""
AOS Stability Dashboard — lightweight server + API.

Serves the dashboard UI and provides a JSON API for reading/updating
the stability roadmap state. Any Claude session can POST updates.

Usage:
    python3 server.py                  # start server (port 4200)
    python3 server.py --port 4201      # custom port
    python3 server.py update p1-2 done # CLI: mark task done
    python3 server.py update p1-2 in-progress --by "session-abc"
    python3 server.py status           # print current state summary
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

STATE_FILE = Path.home() / ".aos" / "data" / "stability-roadmap.json"
DASHBOARD_DIR = Path(__file__).parent
VALID_STATUSES = {"pending", "in-progress", "done", "blocked", "skipped"}


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def find_task(state, task_id):
    for phase in state["phases"]:
        for task in phase["tasks"]:
            if task["id"] == task_id:
                return phase, task
    return None, None


def compute_stats(state):
    phases = []
    total_done = 0
    total_tasks = 0
    for phase in state["phases"]:
        done = sum(1 for t in phase["tasks"] if t["status"] == "done")
        total = len(phase["tasks"])
        total_done += done
        total_tasks += total
        phases.append({
            "id": phase["id"],
            "name": phase["name"],
            "done": done,
            "total": total,
            "pct": round(done / total * 100) if total else 0,
        })
    return {
        "phases": phases,
        "total_done": total_done,
        "total_tasks": total_tasks,
        "total_pct": round(total_done / total_tasks * 100) if total_tasks else 0,
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence request logs

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self._serve_file("dashboard.html", "text/html")
        elif parsed.path == "/api/state":
            state = load_state()
            state["_stats"] = compute_stats(state)
            self._json_response(state)
        elif parsed.path == "/api/stats":
            self._json_response(compute_stats(load_state()))
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/update":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            task_id = body.get("task_id")
            new_status = body.get("status")
            updated_by = body.get("updated_by", "manual")
            details = body.get("details")
            note = body.get("note")

            if not task_id or not new_status:
                self._json_response({"error": "task_id and status required"}, 400)
                return

            if new_status not in VALID_STATUSES:
                self._json_response({"error": f"invalid status, use: {VALID_STATUSES}"}, 400)
                return

            state = load_state()
            phase, task = find_task(state, task_id)
            if not task:
                self._json_response({"error": f"task {task_id} not found"}, 404)
                return

            task["status"] = new_status
            task["updated_at"] = datetime.now(timezone.utc).isoformat()
            task["updated_by"] = updated_by
            if details:
                task["details"] = details
            if note:
                task.setdefault("notes", []).append({
                    "text": note,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "by": updated_by,
                })

            save_state(state)
            self._json_response({"ok": True, "task": task})

        elif parsed.path == "/api/add-note":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            task_id = body.get("task_id")
            note = body.get("note")
            by = body.get("by", "manual")

            if not task_id or not note:
                self._json_response({"error": "task_id and note required"}, 400)
                return

            state = load_state()
            _, task = find_task(state, task_id)
            if not task:
                self._json_response({"error": f"task {task_id} not found"}, 404)
                return

            task.setdefault("notes", []).append({
                "text": note,
                "at": datetime.now(timezone.utc).isoformat(),
                "by": by,
            })
            save_state(state)
            self._json_response({"ok": True})

        else:
            self.send_error(404)

    def _serve_file(self, filename, content_type):
        filepath = DASHBOARD_DIR / filename
        if not filepath.exists():
            self.send_error(404)
            return
        content = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(content)


def cli_update(args):
    """CLI: python3 server.py update <task_id> <status> [--by name] [--note text]"""
    if len(args) < 2:
        print("Usage: server.py update <task_id> <status> [--by name] [--note text]")
        sys.exit(1)

    task_id = args[0]
    new_status = args[1]
    by = "cli"
    note = None

    i = 2
    while i < len(args):
        if args[i] == "--by" and i + 1 < len(args):
            by = args[i + 1]
            i += 2
        elif args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]
            i += 2
        else:
            i += 1

    if new_status not in VALID_STATUSES:
        print(f"Invalid status '{new_status}'. Use: {', '.join(sorted(VALID_STATUSES))}")
        sys.exit(1)

    state = load_state()
    phase, task = find_task(state, task_id)
    if not task:
        print(f"Task '{task_id}' not found.")
        print("Available tasks:")
        for p in state["phases"]:
            for t in p["tasks"]:
                print(f"  {t['id']:8s}  [{t['status']:12s}]  {t['title']}")
        sys.exit(1)

    task["status"] = new_status
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    task["updated_by"] = by
    if note:
        task.setdefault("notes", []).append({
            "text": note, "at": task["updated_at"], "by": by
        })

    save_state(state)
    print(f"✓ {task_id} → {new_status} (in {phase['name']})")


def cli_status():
    """Print a summary of current state."""
    state = load_state()
    stats = compute_stats(state)
    print(f"\n  AOS Stability Roadmap — {stats['total_done']}/{stats['total_tasks']} done ({stats['total_pct']}%)\n")

    status_icons = {
        "pending": "○",
        "in-progress": "◐",
        "done": "●",
        "blocked": "✕",
        "skipped": "–",
    }

    for phase in state["phases"]:
        ps = next(p for p in stats["phases"] if p["id"] == phase["id"])
        print(f"  {phase['name']} ({ps['done']}/{ps['total']})")
        for task in phase["tasks"]:
            icon = status_icons.get(task["status"], "?")
            print(f"    {icon} {task['id']:8s} {task['title']}")
        print()


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "update":
            cli_update(sys.argv[2:])
            return
        elif cmd == "status":
            cli_status()
            return
        elif cmd == "--port":
            port = int(sys.argv[2]) if len(sys.argv) > 2 else 4200
        else:
            try:
                port = int(cmd)
            except ValueError:
                print(f"Unknown command: {cmd}")
                print("Usage: server.py [--port PORT | update | status]")
                sys.exit(1)
    else:
        port = 4200

    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"  Stability Dashboard → http://localhost:{port}")
    print(f"  State file: {STATE_FILE}")
    print(f"  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()

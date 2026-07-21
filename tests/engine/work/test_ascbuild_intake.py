"""Kanban Phase 3 — App Store Connect intake (ascbuild transplant).

Covers, with a FAKE ASC client (no network, no credentials, no xcsym):
  * registry resolution — asc_app_id / bundle_id / fuzzy-name match, web skipped.
  * TestFlight feedback → a pipeline='bug' task (classification=ux) with the
    feedback payload as an activity beat.
  * beta crash → a pipeline='bug' task (severity=1, classification=crash) with
    the symbolication payload as an activity beat.
  * idempotency — a second sync of the same submissions files nothing new.

Isolated via the work_env fixture; symbolicate() is monkeypatched so no xcsym
binary is required.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.engine.work.apps_registry import AppEntry  # noqa: E402
from core.engine.work.intake import ascbuild  # noqa: E402


class FakeASC:
    """Canned App Store Connect responses for the endpoints ascbuild touches."""

    def __init__(self, *, screenshots=None, crashes=None, crashlog=None, builds=None):
        self._screens = screenshots or []
        self._crashes = crashes or []
        self._crashlog = crashlog
        self._builds = builds

    def paged(self, path, max_items=200, **params):
        if path == "/v1/apps":
            return [
                {"id": "111", "attributes": {"name": "Example App",
                                             "bundleId": "com.example.exampleapp"}},
                {"id": "222", "attributes": {"name": "Other App",
                                             "bundleId": "com.example.other"}},
            ]
        if "ScreenshotSubmissions" in path:
            return self._screens
        if "CrashSubmissions" in path:
            return self._crashes
        return []

    def get(self, path, **params):
        if path == "/v1/builds":
            return self._builds or {"data": [{"attributes": {
                "version": "57", "processingState": "VALID"}}], "included": []}
        if "crashLog" in path:
            return {"data": {"attributes": {"logText": self._crashlog or "raw crash"}}}
        return {"data": []}

    def download(self, url, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-bytes")
        return True


# ── registry resolution ──────────────────────────────────────────────────────

def test_resolve_apps_matches_and_skips_web():
    apps = {
        "example-app": AppEntry(id="example-app", name="Example App",
                                bundle_id="com.example.exampleapp"),
        "by-id": AppEntry(id="by-id", name="Whatever", asc_app_id="222"),
        "example-web": AppEntry(id="example-web", name="Example Web", platform="web"),
    }
    resolved = ascbuild.resolve_apps(FakeASC(), apps=apps)
    assert set(resolved) == {"example-app", "by-id"}       # web skipped
    assert resolved["example-app"]["app_id"] == "111"       # bundle match
    assert resolved["by-id"]["app_id"] == "222"             # explicit asc_app_id


# ── feedback → bug task ──────────────────────────────────────────────────────

def test_screenshot_feedback_files_bug(work_env, tmp_path, monkeypatch):
    eng = work_env["engine"]
    monkeypatch.setattr(ascbuild, "MEDIA_DIR", tmp_path / "media")
    asc = FakeASC(screenshots=[{
        "id": "fb1",
        "attributes": {"comment": "The reset button is invisible",
                       "email": "tester@example.com", "deviceModel": "iPhone16,2",
                       "osVersion": "18.0", "createdDate": "2026-07-20T10:00:00Z",
                       "screenshots": [{"fileName": "a.png", "url": "http://x/a.png"}]},
    }])
    app = {"app_id": "111", "name": "Example App"}
    filed = ascbuild.sync_screenshots(asc, "example-app", app, eng, dry=False)
    assert len(filed) == 1

    task = next(t for t in eng.get_all_tasks()
                if (t.get("fields") or {}).get("app") == "example-app")
    assert task["pipeline"] == "bug"
    assert task["stage"] == "new"
    assert task["fields"]["classification"] == "ux"
    assert task["source_ref"] == "testflight:fb1"

    story = eng.get_task_activity(task["id"])
    kinds = [e["kind"] for e in story]
    assert kinds[0] == "created"
    assert "comment" in kinds
    assert "proof" in kinds        # the screenshot attachment
    # Original ASC timestamp preserved on the created beat.
    assert story[0]["ts"] == "2026-07-20T10:00:00Z"

    # Idempotent — second sync files nothing new (already-filed → skipped).
    again = ascbuild.sync_screenshots(asc, "example-app", app, eng, dry=False)
    assert again == []
    tasks = [t for t in eng.get_all_tasks()
             if (t.get("fields") or {}).get("app") == "example-app"]
    assert len(tasks) == 1


def test_crash_files_severity_one_bug(work_env, tmp_path, monkeypatch):
    eng = work_env["engine"]
    monkeypatch.setattr(ascbuild, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(ascbuild, "symbolicate", lambda p: {
        "ok": True, "exception": "EXC_BAD_ACCESS", "pattern": "nil-unwrap",
        "top_frame": "ReaderView.body (ReaderView.swift:42)",
        "app_version": "1.0 (57)", "symbolicated": True,
    })
    asc = FakeASC(crashes=[{
        "id": "cr1",
        "attributes": {"comment": "crashed opening reader", "email": "t@example.com",
                       "deviceModel": "iPhone16,2", "osVersion": "18.0",
                       "createdDate": "2026-07-20T11:00:00Z"},
    }], crashlog="raw crash log")
    app = {"app_id": "111", "name": "Example App"}
    filed = ascbuild.sync_crashes(asc, "example-app", app, eng, dry=False)
    assert len(filed) == 1

    task = next(t for t in eng.get_all_tasks()
                if (t.get("fields") or {}).get("app") == "example-app")
    assert task["fields"]["severity"] == 1
    assert task["fields"]["classification"] == "crash"
    assert task["fields"]["build"] == "57"
    assert "EXC_BAD_ACCESS" in task["fields"]["root_cause"]

    story = eng.get_task_activity(task["id"])
    crash_beat = next(e for e in story if e["kind"] == "comment")
    assert crash_beat["data"]["exception"] == "EXC_BAD_ACCESS"
    assert any(e["kind"] == "proof" and e["data"]["kind"] == "crashlog" for e in story)

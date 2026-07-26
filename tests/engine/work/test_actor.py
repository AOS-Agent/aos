"""
Attribution tests — core/engine/work/actor.py and its wiring.

The bug these guard against is not "attribution is missing". It is worse:
the work adapter defaulted an unset actor to ``"operator"``, so work done by
an agent in an ordinary Claude Code session was recorded as the operator's.
The system did not fail to attribute; it FALSIFIED attribution, convincingly
enough to mislead a reader of the data.

So the load-bearing assertion in this file is the negative one:
**no mutation may ever record "operator" unless the operator is actually
identifiable.** `unknown` is the honest answer and must be preferred.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent.parent.parent / "core" / "engine" / "work")
)

import actor as actor_mod  # noqa: E402


def _history(db_path, task_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM entity_history WHERE entity_id = ? ORDER BY id",
                (task_id,),
            )
        ]
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_actor_env(monkeypatch):
    """No ambient actor signal unless a test asks for one."""
    for var in ("AOS_ACTOR", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                "CLAUDE_SESSION_ID", "CLAUDE_CODE_BRIDGE_SESSION_ID",
                "AOS_AGENT", "CLAUDE_AGENT", "CLAUDE_CODE_AGENT"):
        monkeypatch.delenv(var, raising=False)
    # Never let a real TTY under `pytest -s` turn resolution into "operator".
    monkeypatch.setattr(actor_mod.sys.stdin, "isatty", lambda: False,
                        raising=False)


# ===========================================================================
# Resolution — the five cases, in the contract's order
# ===========================================================================

class TestResolveActor:

    def test_explicit_flag_wins(self, monkeypatch):
        monkeypatch.setenv("AOS_ACTOR", "advisor")
        a = actor_mod.resolve_actor("chief")
        assert (a.kind, a.name) == ("agent", "chief")

    def test_env_var_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("AOS_ACTOR", "agent:advisor")
        a = actor_mod.resolve_actor()
        assert (a.kind, a.name) == ("agent", "advisor")

    def test_claude_session_makes_an_agent(self, monkeypatch):
        """An ordinary Claude Code chat must sign its own work.

        This is the case that was silently landing on the operator.
        """
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess_abc123")
        a = actor_mod.resolve_actor()
        assert a.kind == "agent"
        assert a.session_id == "sess_abc123"
        assert a.name != "operator"

    def test_claude_session_takes_agent_name_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess_abc123")
        monkeypatch.setenv("AOS_AGENT", "steward")
        assert actor_mod.resolve_actor().name == "steward"

    def test_claudecode_flag_alone_is_enough_to_detect_an_agent(self, monkeypatch):
        """CLAUDECODE=1 is set in every session, with no configuration.

        This is what makes the fix work in sessions that already exist, with
        no hook, wrapper or env plumbing to install.
        """
        monkeypatch.setenv("CLAUDECODE", "1")
        a = actor_mod.resolve_actor()
        assert a.kind == "agent"

    def test_agent_session_beats_a_tty(self, monkeypatch):
        """An agent's Bash subprocess can inherit a terminal.

        If the TTY check ran first, agent work would be handed straight back
        to the operator — the falsification, reintroduced.
        """
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setattr(actor_mod.sys.stdin, "isatty", lambda: True)
        assert actor_mod.resolve_actor().kind == "agent"

    def test_interactive_tty_is_the_operator(self, monkeypatch):
        monkeypatch.setattr(actor_mod.sys.stdin, "isatty", lambda: True)
        a = actor_mod.resolve_actor()
        assert (a.kind, a.name) == ("operator", "operator")

    def test_no_signal_is_unknown_never_operator(self):
        """The whole point. No signal must NOT become 'operator'."""
        a = actor_mod.resolve_actor()
        assert a.kind == "unknown", (
            "An unresolvable actor must be 'unknown'. Defaulting to 'operator' "
            "is what falsified the record in the first place."
        )
        assert a.name != "operator"

    def test_cron_and_import_specs_keep_their_kind(self):
        assert actor_mod.resolve_actor("cron:nightly").kind == "cron"
        assert actor_mod.resolve_actor("import:islah").kind == "import"

    def test_bare_name_is_an_agent_not_the_operator(self):
        assert actor_mod.resolve_actor("some-tool").kind == "agent"


# ===========================================================================
# Serialization and vocabulary
# ===========================================================================

class TestVocabulary:

    def test_adapter_string_round_trips(self):
        for spec in ("operator", "agent:chief", "cron:nightly", "unknown"):
            a = actor_mod.resolve_actor(spec)
            assert actor_mod.to_adapter_string(a) == spec

    def test_actor_type_preserves_existing_column_values(self):
        """The column already holds 'operator' and 'agent'. Don't break them."""
        assert actor_mod.actor_type_for(actor_mod.resolve_actor("operator")) == "operator"
        assert actor_mod.actor_type_for(actor_mod.resolve_actor("chief")) == "agent"

    def test_unknown_actor_type_is_not_laundered_into_agent(self):
        unknown = actor_mod.resolve_actor()
        assert actor_mod.actor_type_for(unknown) == "unknown"

    def test_describe_renders_plain_english(self):
        chief = actor_mod.resolve_actor("chief")
        assert actor_mod.describe(chief, "completed", "Draft it") == \
            'Chief completed "Draft it"'
        operator = actor_mod.resolve_actor("operator")
        assert actor_mod.describe(operator, "completed", "Draft it").startswith("You ")
        assert actor_mod.describe(actor_mod.resolve_actor(), "created", "x").startswith(
            "Someone "
        )

    def test_actor_from_dict_rejects_garbage_without_inventing(self):
        assert actor_mod.actor_from_dict(None) is None
        assert actor_mod.actor_from_dict({}) is None


# ===========================================================================
# Persistence — through entity_history, the table that already existed
# ===========================================================================

class TestHistoryIsWritten:

    def test_create_records_who(self, work_env):
        eng = work_env["engine"]
        task = eng.add_task("Sign this", actor="chief")

        rows = _history(work_env["db_path"], task["id"])
        created = [r for r in rows if r["field_name"] == "created"]
        assert created, "Creating a task must record who created it"
        assert created[0]["actor"] == "agent:chief"
        assert created[0]["actor_type"] == "agent"

    def test_source_is_preserved_not_overwritten_by_the_actor(self, work_env):
        """created_by holds a SOURCE enum. Existing consumers read it. Keep it."""
        eng = work_env["engine"]
        task = eng.add_task("Sign this", source="manual", actor="chief")
        assert task["source"] == "manual"

    def test_complete_records_the_status_transition_with_the_actor(self, work_env):
        eng = work_env["engine"]
        task = eng.add_task("Finish this", actor="chief")
        eng.complete_task(task["id"], actor="advisor")

        rows = _history(work_env["db_path"], task["id"])
        done = [r for r in rows
                if r["field_name"] == "status" and r["new_value"] == "done"]
        assert done, "Completing a task must land in entity_history"
        assert done[-1]["actor"] == "agent:advisor"

    def test_start_records_the_actor(self, work_env):
        eng = work_env["engine"]
        task = eng.add_task("Start this", actor="chief")
        eng.start_task(task["id"], actor="advisor")

        rows = _history(work_env["db_path"], task["id"])
        started = [r for r in rows
                   if r["field_name"] == "status" and r["new_value"] == "active"]
        assert started and started[-1]["actor"] == "agent:advisor"

    def test_update_records_one_row_per_changed_field(self, work_env):
        eng = work_env["engine"]
        task = eng.add_task("Rename me", actor="chief")
        eng.update_task(task["id"], actor="advisor", title="Renamed")

        rows = _history(work_env["db_path"], task["id"])
        titles = [r for r in rows if r["field_name"] == "title"]
        assert titles, "A title edit must be recorded"
        assert titles[-1]["new_value"] == "Renamed"
        assert titles[-1]["actor"] == "agent:advisor"

    def test_modified_by_agrees_with_the_history(self, work_env):
        """The column was populated on 72/1927 rows and disagreed with history."""
        eng = work_env["engine"]
        task = eng.add_task("Track me", actor="chief")
        eng.complete_task(task["id"], actor="advisor")

        conn = sqlite3.connect(str(work_env["db_path"]))
        try:
            modified_by = conn.execute(
                "SELECT modified_by FROM tasks WHERE id = ?", (task["id"],)
            ).fetchone()[0]
        finally:
            conn.close()
        assert modified_by == "agent:advisor"

    def test_session_id_is_carried_into_history(self, work_env, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess_xyz789")
        eng = work_env["engine"]
        task = eng.add_task("Session-stamped", actor=None)

        rows = _history(work_env["db_path"], task["id"])
        assert rows[0]["session_id"] == "sess_xyz789"


# ===========================================================================
# THE REGRESSION. This is the one that matters.
# ===========================================================================

class TestNeverSilentlyTheOperator:

    def test_mutation_with_no_actor_signal_records_unknown(self, work_env):
        """No AOS_ACTOR, no explicit actor, no session → 'unknown'.

        Before the fix this recorded 'operator', crediting the human for work
        they never did. Any change that makes this test record 'operator'
        again has reintroduced the falsification.
        """
        eng = work_env["engine"]
        task = eng.add_task("Unattributable")
        eng.complete_task(task["id"])

        rows = _history(work_env["db_path"], task["id"])
        assert rows, "The mutation must still be recorded"
        for row in rows:
            assert row["actor"] != "operator", (
                f"Recorded {row['field_name']!r} as 'operator' with no operator "
                "signal present — this is the falsification bug."
            )
            assert row["actor_type"] != "operator"

    def test_unknown_is_not_laundered_into_agent_either(self, work_env):
        """'unknown' must stay unknown, not get classified as some agent."""
        eng = work_env["engine"]
        task = eng.add_task("Unattributable")

        rows = _history(work_env["db_path"], task["id"])
        assert rows[0]["actor"] == "unknown"
        assert rows[0]["actor_type"] == "unknown"

    def test_agent_session_is_credited_to_the_agent_not_the_operator(
        self, work_env, monkeypatch
    ):
        """The live incident, as a test.

        An agent in an ordinary chat session shells `work done <id>`. It must
        sign as an agent.
        """
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess_live_incident")
        eng = work_env["engine"]
        task = eng.add_task("Part 2: The claim spine")
        eng.complete_task(task["id"])

        rows = _history(work_env["db_path"], task["id"])
        done = [r for r in rows
                if r["field_name"] == "status" and r["new_value"] == "done"]
        assert done, "Completion must be recorded"
        assert done[-1]["actor_type"] == "agent"
        assert done[-1]["actor"] != "operator"
        assert done[-1]["session_id"] == "sess_live_incident"


# ===========================================================================
# Derivation — created_by / started_by / completed_by come out of history
# ===========================================================================

class TestAttributionDerivation:

    def test_derives_the_three_signatures(self, work_env):
        eng = work_env["engine"]
        task = eng.add_task("Full lifecycle", actor="chief")
        eng.start_task(task["id"], actor="advisor")
        eng.complete_task(task["id"], actor="operator")

        att = actor_mod.attribution_for(task["id"])
        assert att["created_by"]["name"] == "chief"
        assert att["started_by"]["name"] == "advisor"
        assert att["completed_by"]["kind"] == "operator"

    def test_absent_rather_than_faked_for_untouched_tasks(self, work_env):
        """~1,900 live tasks have no history. Say nothing, don't guess."""
        eng = work_env["engine"]
        task = eng.add_task("Only created", actor="chief")

        att = actor_mod.attribution_for(task["id"])
        assert "started_by" not in att
        assert "completed_by" not in att

    def test_unknown_task_yields_empty_trail_without_raising(self):
        att = actor_mod.attribution_for("does-not-exist#999")
        assert att["audit"] == []


# ===========================================================================
# Backward compatibility
# ===========================================================================

class TestBackwardCompatibility:

    def test_existing_mutation_signatures_still_work_without_an_actor(self, work_env):
        """Every caller that predates this layer passes no actor. All must work."""
        eng = work_env["engine"]
        task = eng.add_task("Legacy caller")
        assert eng.start_task(task["id"]) is not None
        assert eng.write_handoff(task["id"], "state") is not None
        assert eng.update_task(task["id"], priority=1) is not None
        assert eng.complete_task(task["id"]) is not None

    def test_attribution_follows_the_db_the_caller_is_actually_using(
        self, work_env, monkeypatch, tmp_path
    ):
        """Attribution must land in the same DB as the mutation it describes.

        Some fixtures redirect the engine by patching ``backend.DB_PATH`` and
        never touch ``AOS_WORK_DB``. Resolving the path independently meant the
        adapter wrote to the scratch DB while attribution went to the real
        ``~/.aos/data/work.db`` — which is how a test run came to stamp
        fabricated history onto three live operator tasks.
        """
        eng = work_env["engine"]
        monkeypatch.delenv("AOS_WORK_DB", raising=False)
        decoy = tmp_path / "must-not-be-written.db"

        monkeypatch.setattr(eng, "_resolve_db_path", lambda: decoy)
        task = eng.add_task("Isolation check", actor="chief")

        assert not decoy.exists(), \
            "Attribution resolved its own path instead of the caller's DB"
        assert _history(work_env["db_path"], task["id"]), \
            "History must land in the DB the mutation used"

    def test_attribution_failure_never_breaks_the_mutation(self, work_env,
                                                           monkeypatch):
        """A broken audit write must lose the signature, never the task.

        Breaks it at the connection, the way a real failure would (locked DB,
        missing table), rather than at the guarded writer itself.
        """
        def boom():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(actor_mod, "_db", boom)
        eng = work_env["engine"]
        task = eng.add_task("Survives a broken audit")
        assert eng.get_task(task["id"]) is not None, \
            "The task must survive even when its signature cannot be written"

"""Work-system intake adapters — external sources → ``work.db`` bug tasks.

These adapters TERMINATE in the work system: each one turns an external signal
(an App Store Connect crash, a TestFlight feedback screenshot, a row in the
legacy islah ``bugs.yaml`` ledger) into a ``pipeline='bug'`` task on the board,
with a faithful ``task_activity`` narrative.

Why they live under ``core/engine/work/`` and not ``core/engine/comms/``: the
``comms`` layer is the person-to-person message plane — channel adapters
(whatsmeow, slack, imessage) that feed ``comms.db`` with human messages and
resolve senders to people. An App Store Connect → work-task intake is a
category-different thing: it produces work items, shares the work layer's app
registry (``apps_registry``) and bug pipeline (``pipelines``), and never touches
a person or a message. Its honest home is beside the machinery it feeds.

Modules:
  * ``bug_tasks``    — the shared "file one bug into work.db" primitive: create
                       the task (``narrate=False``) and reconstruct its activity
                       narrative with ORIGINAL timestamps, idempotently.
  * ``islah_import`` — one-shot + mirror import of the islah ``bugs.yaml`` ledger.
  * ``ascbuild``     — App Store Connect intake (TestFlight feedback + beta
                       crashes, symbolicated), transplanted from islah,
                       config-driven via ``apps_registry``.
"""

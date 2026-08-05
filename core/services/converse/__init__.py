"""Converse — supervisor daemon package (Wave 2 / T3).

    main.py         LaunchAgent entrypoint (com.aos.converse)
    supervisor.py   Supervisor — the received->handling->handled|failed
                     loop, retry/backoff, crash-sweep, reauth pausing
    notify.py        Operator notifications (Telegram, via the shared router)

Ships OFF — see main.py's docstring and core/infra/migrations/
101_converse_service.py. Consumes core/engine/comms/converse/* (Wave 0/1)
without modifying it.
"""

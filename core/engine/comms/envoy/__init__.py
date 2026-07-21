"""Envoy — autonomous outbound conversations on the operator's behalf.

The operator commissions a mission ("talk to <contact>, get <goal> done").
Envoy opens the conversation over iMessage introducing itself as the
operator's AI agent, watches comms.db for replies, runs headless Claude
turns to converse until the mission completes, escalates when uncertain,
and notifies the operator on Telegram.

Sibling of sentinel/ (reactive, operator-voice) — envoy is proactive and
speaks transparently AS an agent. Entry: the `envoy` skill + `envoy` CLI.
"""

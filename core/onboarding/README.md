# AOS Onboarding

Conversational first-time setup, triggered by Chief when it detects a fresh install
(no `~/.aos/config/operator.yaml`).

## Stages

1. **Identity** — Who are you? What's your role? What do you do?
2. **Essentials** — Telegram bot token, vault location
3. **Communication** — WhatsApp, email, calendar
4. **Your Work** — Initial goals, projects, what's on your plate
5. **Agents** — Which catalog agents to activate
6. **Activate** — Start services, verify health, first message

## How it works

Each stage reads integration manifests from `core/infra/integrations/*/manifest.yaml`
and walks the user through the setup steps defined there.

Progress is saved to `~/.aos/config/onboarding-state.yaml` so it can resume
if interrupted.

## Status

Framework designed. Conversational flow not yet built.

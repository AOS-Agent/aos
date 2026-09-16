# iMessage — send and read for the operator

Two tools. Both only work for contacts listed in `~/.aos/config/comms.yaml`.

## Send

When the operator says any of these:

- "message Hisham: running 10 min late"
- "text Hisham …" / "tell Hisham …" / "iMessage Hisham …"

run:

```bash
python3 ~/aos/core/bin/cli/comms-send "Hisham" "running 10 min late"
```

Rules:

- Send the text **exactly as the operator wrote it**. Never rephrase, never add.
- If the operator gives intent instead of text ("tell Hisham I'm late"), draft
  one message, show it, and send only after they confirm.
- `--dry-run` shows who it would go to without sending. Use it if the name is
  ambiguous.
- Exit 2 with `denied: … not in the iMessage allowlist` means the contact is
  not configured. Report that line and stop. Do not send another way, and do
  not edit the allowlist on your own.
- Exit 1 means Messages.app refused the send. Report it; retry once at most.

## Read

When the operator asks "what did Hisham say", "show my thread with Hisham",
"any reply from Hisham":

```bash
python3 ~/aos/core/bin/cli/comms-thread "Hisham" --days 7
```

`--days N` and `--limit N` widen the window; `--json` for machine output.
Exit 1 with `chat.db: not readable` means Full Disk Access is missing for the
terminal app; tell the operator, do not work around it.

## What this is not

- Not Envoy or Sentinel. Those hold conversations on their own and ship off by
  default. `comms-send` sends one message the operator asked for.
- Not `comms-recall`. That searches the ingested history in `comms.db`;
  `comms-thread` reads the live thread from Messages.

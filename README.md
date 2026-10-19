# discord-tracker

Monitors new Discord messages in real time and forwards **high-intent hiring/client opportunities** to a Discord webhook.

> Important: this project uses **user tokens** (self-bot style). That can violate Discord ToS and may get accounts banned. Use at your own risk.

## Setup

Install deps:

```bash
python -m pip install -r requirements.txt
```

## Configure (environment variables)

Copy `.env.example` to `.env` and fill in your values.

You can configure **two webhooks**:
- `STATUS_WEBHOOK_URL`: restart/startup logs (your “bot webhook”)
- `JOBS_WEBHOOK_URL`: hiring/opportunity alerts (your “job webhook”)

### Tokens (choose one option in `.env`)

#### Option A: Single token

```powershell
$env:DISCORD_TOKEN="YOUR_DISCORD_USER_TOKEN"
$env:DISCORD_ACCOUNT_NAME="Main Account"
```

#### Option B: Multiple tokens (JSON)

`DISCORD_TOKENS_JSON` must be **one line** in `.env` (multi-line breaks parsing):

```powershell
$env:DISCORD_TOKENS_JSON='[{"name":"Main Account","token":"TOKEN_1"},{"name":"Alt Account","token":"TOKEN_2"}]'
```

## Block spam and users

Edit `blockedusers.json`:

- **`user_ids`**: Discord user IDs to never alert on (right-click user → Copy User ID with Developer Mode on).
- **`guild_ids`** / **`channel_ids`**: optional; skip entire servers or channels.
- **`blocked_phrases`**: if the message contains any phrase (case-insensitive), no alert (scam, translator spam, low-effort bids, etc.).
- **`blocked_regex`**: optional list of regex strings (e.g. `"telegram\\.me/\\w+"`).

If the file is missing, the tracker runs with an empty blocklist.

## Admin commands (Discord channel + status webhook fallback)

Discord **webhooks cannot receive** channel messages, so commands are read by your **monitor account** in a text channel you choose.

Set in `.env`:

- **`CMD_CHANNEL_ID`** (optional) — if set, commands are only read in that channel and **no job alerts** are sent from that channel. If **unset**, commands work in **any** text channel (still optional **`CMD_GUILD_ID`** server filter); job alerts still run from those channels when messages score.
- **`CMD_OPERATOR_USER_IDS`** (optional) — extra Discord user IDs allowed to run commands (comma-separated).
- **`CMD_SELF_ADMIN`** (default `1`) — when enabled, the **command-handler** account (the monitor token chosen below) is always allowed to run commands as that user, so you can use a **dedicated cmd token** without listing your user ID.
- **`CMD_GUILD_ID`** (optional) — only accept commands in this server.
- **`CMD_OPERATOR_TOKEN`** (recommended) — **one** user token that runs all `!` commands. If this exact string appears in `DISCORD_TOKENS_JSON`, only that session handles commands (no duplicate login). If it is **not** in the JSON list, a **second** client logs in with this token and **only** listens for commands (no job webhooks). Typo alias: **`CMD_OPREATOR_TOKEN`**.
- **`CMD_HANDLER_ACCOUNT_NAME`** (fallback) — if `CMD_OPERATOR_TOKEN` is unset, which monitor account runs commands. Defaults to the **first** token in `DISCORD_TOKENS_JSON`.
- **`CMD_PREFIX`** (optional, default `!`).

Replies are posted **in the channel**; if sending fails, a short note goes to **`STATUS_WEBHOOK_URL`**.

Commands (prefix + name, e.g. `!help`):

| Command | Action |
|--------|--------|
| `!block <user_id>` | Append to `blockedusers.json` → `user_ids` |
| `!unblock <user_id>` | Remove user id |
| `!blockserver` | Block **this** server (no job alerts). Or `!blockserver <server_id>` |
| `!unblockserver` | Unblock this server (or pass a server id) |
| `!blockphrase <text>` | Append to `blocked_phrases` |
| `!unblockphrase <text>` | Remove phrase (case-insensitive match) |
| `!kw add <list> <phrase>` | Add phrase to a list in `keywords.json` (`primary`, `tech`, `pay`, or full key name) |
| `!kw remove <list> <phrase>` | Remove exact phrase from list |
| `!wb add <token>` / `!wb remove <token>` | Edit `special_patterns.word_boundary` |

After each change, all monitors **reload** `blockedusers.json` and `keywords.json` from disk.

### Auto-restart when `.env` changes

By default, saving `.env` triggers a **full restart** (needed for new tokens, webhooks, etc.). You should see `[*] .env changed — restarting monitor...` in the console, then a new startup ping.

- **`ENV_AUTO_RESTART=0`** — disable this
- **`ENV_AUTO_RESTART_GRACE_SECONDS=15`** — ignore `.env` for this long after start (avoids false restarts)
- **`ENV_AUTO_RESTART_POLL_SECONDS=2`** — how often to check the file (default `2`)

Place `.env` in the **same folder** you run the app from (next to the EXE if you use the build).

## AI filter (Ollama Cloud)

Optional **second stage** after keyword scoring. Only posts that look like **developer hiring / development tasks** are forwarded.

1. Create an API key at [ollama.com](https://ollama.com)
2. In `.env`:

```env
OLLAMA_FILTER=1
OLLAMA_API_KEY=your_key
OLLAMA_BASE_URL=https://ollama.com
OLLAMA_MODEL=gemma4:31b-cloud
```

Console shows `[OLLAMA] accept|reject|...` for each keyword hit. Set `OLLAMA_FILTER=0` to disable.

## Keywords

Edit `keywords.json` to tune:
- trigger phrases (client/hiring intent)
- supply-side phrases (people offering services)
- soft triggers (only alert when enough context exists)
- tags (tech/budget/location) + scoring thresholds

## Run

```bash
python main.py
```

If everything is working you should see:
- `Starting all self-bots...`
- `[OK] <AccountName> is online | <discord_user>`

By default, each time you start `main.py` it also posts a small **startup test** message to your webhook.
You can disable that with `STARTUP_TEST_WEBHOOK=0` in `.env`.

When `main.py` stops (normal exit / Ctrl+C), it posts a **shutdown** log to your status webhook.
You can disable that with `SHUTDOWN_LOG_WEBHOOK=0` in `.env`.

**Offline detection:** abrupt kills (`taskkill /F`, power loss) cannot send a webhook. For day-to-day visibility:

- **Heartbeat** (default **on**): every `STATUS_HEARTBEAT_SECONDS` (default **300** = 5 min) a small **🟢 Monitor heartbeat** embed is posted to `STATUS_WEBHOOK_URL`. If you see **no heartbeat for ~10+ minutes** while the app should be running, treat it as **offline**.
- **Shutdown hooks:** `SIGINT` / `SIGTERM` / `SIGBREAK` (where supported), Windows console **close (X)**, `atexit`, and the script `finally` block all try the same **🔴 offline** webhook (deduplicated).

Tune with `STATUS_HEARTBEAT_WEBHOOK=0` to disable heartbeats, or set `STATUS_HEARTBEAT_SECONDS=600` for a 10-minute interval.

## Build a Windows EXE

This uses PyInstaller to create `dist\discord-tracker.exe`.

```powershell
.\build.ps1
```

If you want a background/no-window build (harder to stop), use:

```powershell
.\build.ps1 -NoConsole
```

## Notes

- If you previously stored secrets in `tokens.json`, **rotate/revoke your tokens**.


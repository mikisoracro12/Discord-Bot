# Discord Task Finder

A Discord bot that monitors configured Discord channels and forwards high-quality software-development / hiring task posts to your own Discord server.

## Features

- Hybrid classification: keyword prefilter + optional AI (Ollama Cloud / OpenAI-compatible)
- Keyword/phrase matching
- Weighted scoring
- Context-aware exclusions
- Scam/irrelevant filtering
- Software category detection
- Technology extraction
- Budget extraction
- SQLite duplicate prevention
- Discord embeds with jump links
- Configurable YAML keywords
- Pytest tests

## 1. Requirements

- Python 3.12+
- A Discord bot
- Permission to add the bot to source servers
- A destination channel in your own server

## 2. Discord Developer Portal

Create an application and bot in the Discord Developer Portal.

Enable:

**Bot → Privileged Gateway Intents → Message Content Intent**

The bot needs access to the source channels and:

- View Channels
- Read Message History

For the destination channel:

- View Channel
- Send Messages
- Embed Links

Do not give Administrator unless you have a specific reason.

## 3. Install

Windows:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux/macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Configure

Copy `.env.example` to `.env`.

Example:

```env
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN
SOURCE_CHANNEL_IDS=123456789012345678,234567890123456789
DESTINATION_CHANNEL_ID=345678901234567890
DATABASE_URL=sqlite+aiosqlite:///tasks.db
TASK_THRESHOLD=70
HIGH_CONFIDENCE_THRESHOLD=90

AI_ENABLED=true
AI_API_KEY=YOUR_OLLAMA_API_KEY
AI_BASE_URL=https://ollama.com/v1
AI_MODEL=gemma4:31b
AI_PREFILTER_THRESHOLD=25
AI_TIMEOUT_SECONDS=30
```

Never commit `.env`.

Create an API key at [ollama.com/settings/keys](https://ollama.com/settings/keys). Set `AI_ENABLED=false` to use rules only.

Defaults target **Ollama Cloud**. For a local Ollama server instead:

```env
AI_BASE_URL=http://localhost:11434/v1
AI_API_KEY=ollama
AI_MODEL=llama3.2
```

Any OpenAI-compatible endpoint works if you change `AI_BASE_URL` and `AI_MODEL`.

## 5. Invite the bot

Create an OAuth2 install/invite URL with the `bot` scope and the minimum permissions described above.

The bot must be installed in every source server it needs to monitor. It cannot read arbitrary servers where it is not a member.

## 6. Configure channels

Put source channel IDs into:

```env
SOURCE_CHANNEL_IDS=
```

Put your own destination channel ID into:

```env
DESTINATION_CHANNEL_ID=
```

The bot ignores messages outside configured source channels.

## 7. Run

```bash
python main.py
```

## 8. Tests

```bash
pytest -q
```

## 9. Improve matching

Edit:

```text
config/keywords.yaml
```

You can add:

- task phrases
- software terms
- development actions
- exclusions
- scam phrases
- collaboration phrases
- recruiting phrases
- categories

No Python code change is required for normal keyword updates.

## 10. How scoring works

**Rules (always run first):** positive points for task intent, software terms, development actions, payment/budget; negative points for collaboration, recruiting, scam, and irrelevant-job patterns.

**Hybrid AI (when `AI_ENABLED=true` and `AI_API_KEY` is set):**

1. Rules score the message.
2. A cheap prefilter decides whether the message is worth an AI call (`AI_PREFILTER_THRESHOLD`).
3. Obvious junk never hits the API.
4. Candidate messages are classified by the AI for hiring / freelance / commission software tasks.
5. If the AI call fails, the bot falls back to the rule result.

Accepted tasks are forwarded as embeds that include a jump link to the original Discord message.

When uncertain, the bot prefers rejection over forwarding junk.

## 11. Important limitation

The bot can only read messages in Discord servers/channels where it has been installed and has permission to read them.

It cannot monitor arbitrary Discord servers invisibly.

## 12. Security

Never publish your Discord bot token.

If the token is exposed, regenerate it immediately in the Discord Developer Portal.

import discord
import json
import asyncio
import hashlib
import random
import re
import aiohttp
import os
import signal
import sys
import subprocess
import time
from dotenv import load_dotenv
from datetime import datetime, timezone
import platform
import textwrap
import atexit
import ctypes
import urllib.request
import urllib.error

_shutdown_sent = False
_intentional_restart = False
_exit_hooks_installed = False

def _post_webhook_sync(webhook_url: str, payload: dict, timeout_s: float = 8.0) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "discord-tracker"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        # Discord typically returns 204 for webhooks
        if resp.status not in (200, 204):
            raise RuntimeError(f"Webhook HTTP {resp.status}")

def _send_shutdown_sync_best_effort() -> None:
    global _shutdown_sent
    if _shutdown_sent or _intentional_restart:
        return
    if not _env_truthy(os.getenv("SHUTDOWN_LOG_WEBHOOK"), default=True):
        _shutdown_sent = True
        return
    load_dotenv()
    status_url = ""
    payload: dict | None = None
    try:
        tokens, _keywords, _jobs_webhook_url, status_url = load_config()
        payload = build_shutdown_embed_payload(tokens=tokens)
    except Exception:
        # Still try a minimal offline ping if only env URLs are readable
        status_url = (os.getenv("STATUS_WEBHOOK_URL") or os.getenv("JOBS_WEBHOOK_URL") or "").strip()
        if status_url:
            payload = build_shutdown_embed_payload(tokens=None)
    if not status_url or not payload:
        _shutdown_sent = True
        return
    for attempt in range(3):
        try:
            _post_webhook_sync(status_url, payload)
            time.sleep(0.25)
            break
        except Exception:
            if attempt < 2:
                time.sleep(0.75)
    _shutdown_sent = True

def _register_windows_close_handler() -> None:
    # Best-effort capture for closing the console window (X), logoff, shutdown.
    if os.name != "nt":
        return

    handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def _handler(ctrl_type: int) -> bool:
        # 0=CTRL_C_EVENT, 1=CTRL_BREAK_EVENT, 2=CTRL_CLOSE_EVENT,
        # 5=CTRL_LOGOFF_EVENT, 6=CTRL_SHUTDOWN_EVENT
        _send_shutdown_sync_best_effort()
        time.sleep(0.35)
        return False  # allow default handling to continue (process exits)

    try:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler_type(_handler), True)
    except Exception:
        pass

# Load configs
def load_config():
    load_dotenv()
    with open('keywords.json', 'r', encoding='utf-8') as f:
        keywords = json.load(f)

    jobs_webhook_url = os.getenv("JOBS_WEBHOOK_URL")
    status_webhook_url = os.getenv("STATUS_WEBHOOK_URL") or jobs_webhook_url
    if not jobs_webhook_url:
        raise RuntimeError("Missing JOBS_WEBHOOK_URL env var (set it in .env).")
    if not status_webhook_url:
        raise RuntimeError("Missing STATUS_WEBHOOK_URL env var (set it in .env).")

    tokens_json = os.getenv("DISCORD_TOKENS_JSON")
    if tokens_json:
        # NOTE: .env variables must be single-line. Multi-line DISCORD_TOKENS_JSON
        # will be read as just "[" by python-dotenv and break JSON parsing.
        if tokens_json.strip() in {"[", "]"}:
            raise RuntimeError(textwrap.dedent("""
                DISCORD_TOKENS_JSON is invalid (looks like a multi-line value).

                Put it on ONE line in .env, for example:

                DISCORD_TOKENS_JSON=[{"name":"Main","token":"TOKEN_1"},{"name":"Alt","token":"TOKEN_2"}]
            """).strip())

        try:
            tokens = json.loads(tokens_json)
        except json.JSONDecodeError as e:
            raise RuntimeError(textwrap.dedent(f"""
                DISCORD_TOKENS_JSON is not valid JSON: {e}

                Expected format (single line):
                DISCORD_TOKENS_JSON=[{{"name":"Main","token":"TOKEN_1"}},{{"name":"Alt","token":"TOKEN_2"}}]
            """).strip()) from e
    else:
        single_token = os.getenv("DISCORD_TOKEN")
        if single_token:
            tokens = [{
                "name": os.getenv("DISCORD_ACCOUNT_NAME", "Main Account"),
                "token": single_token,
            }]
        else:
            raise RuntimeError(
                "Missing DISCORD_TOKEN or DISCORD_TOKENS_JSON env var (set one in .env)."
            )

    return tokens, keywords, jobs_webhook_url, status_webhook_url

def normalize_blocklist(raw) -> dict:
    if not isinstance(raw, dict):
        raw = {}

    def _int_set(key: str) -> set[int]:
        out: set[int] = set()
        for x in raw.get(key, []) or []:
            try:
                out.add(int(x))
            except (TypeError, ValueError):
                pass
        return out

    phrases: list[str] = []
    for p in raw.get("blocked_phrases", []) or []:
        s = str(p).strip().lower()
        if len(s) >= 3:
            phrases.append(s)

    regex_list: list[re.Pattern[str]] = []
    for p in raw.get("blocked_regex", []) or []:
        try:
            regex_list.append(re.compile(str(p), re.IGNORECASE))
        except re.error:
            pass

    return {
        "user_ids": _int_set("user_ids"),
        "guild_ids": _int_set("guild_ids"),
        "channel_ids": _int_set("channel_ids"),
        "phrases": phrases,
        "regex": regex_list,
    }

def load_blocklist() -> dict:
    path = "blockedusers.json"
    if not os.path.isfile(path):
        return normalize_blocklist({})
    with open(path, "r", encoding="utf-8") as f:
        return normalize_blocklist(json.load(f))

def message_is_blocked(message: discord.Message, bl: dict) -> bool:
    if bl["user_ids"] and message.author.id in bl["user_ids"]:
        return True
    if message.guild and bl["guild_ids"] and message.guild.id in bl["guild_ids"]:
        return True
    if bl["channel_ids"] and message.channel and message.channel.id in bl["channel_ids"]:
        return True
    text = (message.content or "").lower()
    for p in bl["phrases"]:
        if p in text:
            return True
    body = message.content or ""
    for rx in bl["regex"]:
        if rx.search(body):
            return True
    return False

# --- Admin commands (cmd channel on Discord; persists JSON on disk) ---
KEYWORD_LIST_KEYS = frozenset({
    "triggers_primary",
    "triggers_freelance",
    "triggers_supply",
    "soft_triggers",
    "triggers_hidden_gold",
    "role_keywords",
    "tech_stack",
    "work_type_location",
    "budget_payment",
})

KEYWORD_LIST_ALIASES = {
    "primary": "triggers_primary",
    "freelance": "triggers_freelance",
    "supply": "triggers_supply",
    "soft": "soft_triggers",
    "hidden": "triggers_hidden_gold",
    "roles": "role_keywords",
    "tech": "tech_stack",
    "location": "work_type_location",
    "loc": "work_type_location",
    "pay": "budget_payment",
}


def resolve_keyword_list(name: str) -> str | None:
    n = (name or "").strip().lower()
    if not n:
        return None
    if n in KEYWORD_LIST_KEYS:
        return n
    return KEYWORD_LIST_ALIASES.get(n)


def _read_json_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _write_json_file(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_keywords_disk() -> dict:
    return _read_json_file("keywords.json")


def load_blockedusers_disk() -> dict:
    if not os.path.isfile("blockedusers.json"):
        return {
            "user_ids": [],
            "guild_ids": [],
            "channel_ids": [],
            "blocked_phrases": [],
            "blocked_regex": [],
        }
    return _read_json_file("blockedusers.json")


class ConfigHub:
    """Shared reload for all monitor clients after JSON edits."""

    def __init__(self, status_webhook_url: str | None):
        self.clients: list["HiringMonitor"] = []
        self.shutdown_clients: list[discord.Client] = []
        self.status_webhook_url = status_webhook_url or ""
        self.reload_lock = asyncio.Lock()
        self.cmd_handler_user_id: int | None = None

    def register_client(self, client: discord.Client) -> None:
        self.shutdown_clients.append(client)

    async def close_all_clients(self) -> None:
        for client in self.shutdown_clients:
            try:
                await client.close()
            except Exception:
                pass

    async def reload_all_from_disk(self) -> None:
        async with self.reload_lock:
            bl = load_blocklist()
            raw_kw = load_keywords_disk()
            for c in self.clients:
                c.blocklist = bl
                c.keywords = normalize_keywords_config(raw_kw)


def parse_cmd_env() -> dict:
    """Admin command settings from env (optional).

    If ``CMD_CHANNEL_ID`` is unset, commands are accepted in any guild text channel
    (still filtered by ``CMD_GUILD_ID`` when set). If set, commands are limited to
    that channel and non-handler monitors skip job scanning there only.
    """
    load_dotenv()
    raw_ch = os.getenv("CMD_CHANNEL_ID", "").strip()
    raw_guild = os.getenv("CMD_GUILD_ID", "").strip()
    raw_ops = os.getenv("CMD_OPERATOR_USER_IDS", "").strip()
    prefix = os.getenv("CMD_PREFIX", "!").strip() or "!"

    operators: set[int] = set()
    for part in re.split(r"[,\s]+", raw_ops):
        if not part:
            continue
        try:
            operators.add(int(part))
        except ValueError:
            continue

    try:
        channel_id = int(raw_ch) if raw_ch else 0
    except ValueError:
        channel_id = 0

    guild_id: int | None = None
    if raw_guild:
        try:
            guild_id = int(raw_guild)
        except ValueError:
            guild_id = None

    # CMD_SELF_ADMIN=1 (default): the command-handler token's own Discord user may run commands
    # without listing CMD_OPERATOR_USER_IDS.
    sa = os.getenv("CMD_SELF_ADMIN", "1").strip().lower()
    self_admin = sa not in {"0", "false", "no", "off", ""}

    enabled = bool(operators or self_admin)
    return {
        "enabled": enabled,
        "channel_id": channel_id,
        "guild_id": guild_id,
        "operators": operators,
        "prefix": prefix,
        "self_admin": self_admin,
    }


async def reply_cmd_message(
    message: discord.Message,
    hub: "ConfigHub",
    text: str,
) -> None:
    text = (text or "")[:2000]
    try:
        await message.channel.send(text)
        return
    except Exception:
        pass
    if hub.status_webhook_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    hub.status_webhook_url,
                    json={"content": f"[cmd] {text[:1800]}"},
                ) as resp:
                    if resp.status not in (200, 204):
                        pass
        except Exception:
            pass


async def reply_cmd_embed(
    message: discord.Message,
    hub: "ConfigHub",
    embed: discord.Embed,
) -> None:
    try:
        await message.channel.send(embed=embed)
        return
    except Exception:
        pass
    if hub.status_webhook_url:
        try:
            payload = {"embeds": [embed.to_dict()]}
            async with aiohttp.ClientSession() as session:
                async with session.post(hub.status_webhook_url, json=payload) as resp:
                    if resp.status not in (200, 204):
                        pass
        except Exception:
            try:
                plain = (embed.title or "Command") + "\n" + (embed.description or "")
                for field in embed.fields:
                    plain += f"\n{field.name}\n{field.value}"
                plain = plain[:1900]
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        hub.status_webhook_url,
                        json={"content": f"[cmd] {plain}"},
                    ) as resp:
                        if resp.status not in (200, 204):
                            pass
            except Exception:
                pass


_cmd_process_lock = asyncio.Lock()


async def process_admin_command(
    message: discord.Message,
    hub: "ConfigHub",
    *,
    handler_user_id: int | None = None,
) -> bool:
    """
    Returns True if message was handled as a command (caller should not treat as job text).
    """
    settings = parse_cmd_env()
    if not settings["enabled"]:
        return False
    if settings["channel_id"] and message.channel.id != settings["channel_id"]:
        return False
    if settings["guild_id"] is not None and message.guild and message.guild.id != settings["guild_id"]:
        return False

    allowed: set[int] = set(settings["operators"])
    if settings.get("self_admin") and handler_user_id is not None:
        allowed.add(handler_user_id)
    if message.author.id not in allowed:
        return False

    content = (message.content or "").strip()
    prefix = settings["prefix"]
    if not content.startswith(prefix):
        return False

    rest = content[len(prefix) :].strip()
    if not rest:
        await reply_cmd_message(message, hub, f"Commands use prefix `{prefix}`. Try `{prefix}help`.")
        return True

    parts = rest.split()
    cmd = parts[0].lower() if parts else ""

    async with _cmd_process_lock:
        try:
            if cmd == "help":
                p = prefix
                embed = discord.Embed(
                    title="Command menu",
                    description=f"Put **{p}** before each command. Only your trusted accounts can use this.",
                    color=0x5865F2,
                )
                embed.add_field(
                    name="Help",
                    value=f"**{p}help** — Show this menu",
                    inline=False,
                )
                embed.add_field(
                    name="Ignore someone",
                    value=(
                        f"**{p}block** `user id` — Stop alerts from that Discord user\n"
                        f"**{p}unblock** `user id` — Start watching them again"
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="Ignore a server",
                    value=(
                        f"**{p}blockserver** — Stop alerts from this server (or `{p}blockserver` `server id`)\n"
                        f"**{p}unblockserver** — Watch this server again (or pass a server id)"
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="Ignore messy text",
                    value=(
                        f"**{p}blockphrase** `some text` — Skip messages containing that phrase\n"
                        f"**{p}unblockphrase** `some text` — Remove that phrase rule"
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="Tune job keywords",
                    value=(
                        f"**{p}kw add** `list` `phrase` — Add words the tracker watches for\n"
                        f"**{p}kw remove** `list` `phrase` — Remove that phrase\n"
                        f"*Example:* `{p}kw add tech react`"
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="List names for `kw`",
                    value=(
                        "Short names: **primary**, **freelance**, **supply**, **soft**, **hidden**, "
                        "**roles**, **tech**, **location** (or **loc**), **pay**"
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="Whole-word matches",
                    value=(
                        f"**{p}wb add** `word` — Match only full words (e.g. slang tickers)\n"
                        f"**{p}wb remove** `word` — Drop that word"
                    ),
                    inline=False,
                )
                embed.set_footer(text="If nothing appears here, check your status webhook for the same menu.")
                await reply_cmd_embed(message, hub, embed)
                return True

            if cmd == "block":
                if len(parts) < 2:
                    await reply_cmd_message(message, hub, "Usage: `block <user_id>`")
                    return True
                uid = int(parts[1])
                data = load_blockedusers_disk()
                ids = data.setdefault("user_ids", [])
                if not isinstance(ids, list):
                    ids = []
                    data["user_ids"] = ids
                if uid not in ids:
                    ids.append(uid)
                _write_json_file("blockedusers.json", data)
                await hub.reload_all_from_disk()
                await reply_cmd_message(message, hub, f"OK — blocked user `{uid}`")
                return True

            if cmd == "unblock":
                if len(parts) < 2:
                    await reply_cmd_message(message, hub, "Usage: `unblock <user_id>`")
                    return True
                uid = int(parts[1])
                data = load_blockedusers_disk()
                ids = data.get("user_ids", [])
                if isinstance(ids, list) and uid in ids:
                    ids.remove(uid)
                data["user_ids"] = ids
                _write_json_file("blockedusers.json", data)
                await hub.reload_all_from_disk()
                await reply_cmd_message(message, hub, f"OK — unblocked user `{uid}`")
                return True

            if cmd in {"blockserver", "blockguild"}:
                if len(parts) >= 2:
                    gid = int(parts[1])
                elif message.guild:
                    gid = message.guild.id
                else:
                    await reply_cmd_message(
                        message, hub, "Usage: `blockserver <server_id>` or run this inside a server."
                    )
                    return True
                data = load_blockedusers_disk()
                ids = data.setdefault("guild_ids", [])
                if not isinstance(ids, list):
                    ids = []
                    data["guild_ids"] = ids
                if gid not in ids:
                    ids.append(gid)
                _write_json_file("blockedusers.json", data)
                await hub.reload_all_from_disk()
                name = message.guild.name if message.guild and message.guild.id == gid else str(gid)
                await reply_cmd_message(message, hub, f"OK — blocked server **{name}** (`{gid}`)")
                return True

            if cmd in {"unblockserver", "unblockguild"}:
                if len(parts) >= 2:
                    gid = int(parts[1])
                elif message.guild:
                    gid = message.guild.id
                else:
                    await reply_cmd_message(
                        message, hub, "Usage: `unblockserver <server_id>` or run this inside a server."
                    )
                    return True
                data = load_blockedusers_disk()
                ids = data.get("guild_ids", [])
                if not isinstance(ids, list):
                    ids = []
                if gid in ids:
                    ids.remove(gid)
                data["guild_ids"] = ids
                _write_json_file("blockedusers.json", data)
                await hub.reload_all_from_disk()
                await reply_cmd_message(message, hub, f"OK — unblocked server `{gid}`")
                return True

            if cmd == "blockphrase":
                phrase = rest[len(parts[0]) :].strip()
                if len(phrase) < 3:
                    await reply_cmd_message(message, hub, "Phrase must be at least 3 characters.")
                    return True
                lowered = phrase.lower()
                data = load_blockedusers_disk()
                phrases = data.setdefault("blocked_phrases", [])
                if not isinstance(phrases, list):
                    phrases = []
                    data["blocked_phrases"] = phrases
                if lowered not in [str(p).lower() for p in phrases]:
                    phrases.append(phrase)
                _write_json_file("blockedusers.json", data)
                await hub.reload_all_from_disk()
                await reply_cmd_message(message, hub, f"OK — added block phrase `{phrase[:200]}`")
                return True

            if cmd == "unblockphrase":
                phrase = rest[len(parts[0]) :].strip()
                if not phrase:
                    await reply_cmd_message(message, hub, "Usage: `unblockphrase <text>`")
                    return True
                data = load_blockedusers_disk()
                phrases = data.get("blocked_phrases", [])
                if not isinstance(phrases, list):
                    phrases = []
                lowered = phrase.lower()
                new_list = [p for p in phrases if str(p).lower() != lowered]
                data["blocked_phrases"] = new_list
                _write_json_file("blockedusers.json", data)
                await hub.reload_all_from_disk()
                await reply_cmd_message(message, hub, "OK — removed phrase (if it existed)")
                return True

            if cmd == "kw":
                if len(parts) < 4 or parts[1].lower() not in {"add", "remove"}:
                    await reply_cmd_message(
                        message, hub, f"Usage: `{prefix}kw add <list> <phrase>` or `{prefix}kw remove <list> <phrase>`"
                    )
                    return True
                op = parts[1].lower()
                raw_list = parts[2]
                key = resolve_keyword_list(raw_list)
                if not key:
                    await reply_cmd_message(message, hub, f"Unknown list `{raw_list}`. Try `{prefix}help`.")
                    return True
                phrase = " ".join(parts[3:]).strip()
                if len(phrase) < 2:
                    await reply_cmd_message(message, hub, "Phrase too short.")
                    return True
                kw_data = load_keywords_disk()
                lst = kw_data.setdefault(key, [])
                if not isinstance(lst, list):
                    lst = []
                    kw_data[key] = lst
                if op == "add":
                    if phrase not in lst:
                        lst.append(phrase)
                else:
                    kw_data[key] = [p for p in lst if str(p) != phrase]
                _write_json_file("keywords.json", kw_data)
                await hub.reload_all_from_disk()
                await reply_cmd_message(message, hub, f"OK — keywords `{key}` updated")
                return True

            if cmd == "wb":
                if len(parts) < 3 or parts[1].lower() not in {"add", "remove"}:
                    await reply_cmd_message(message, hub, f"Usage: `{prefix}wb add <token>` / `{prefix}wb remove <token>`")
                    return True
                op = parts[1].lower()
                token = " ".join(parts[2:]).strip()
                if len(token) < 1:
                    await reply_cmd_message(message, hub, "Token required.")
                    return True
                kw_data = load_keywords_disk()
                sp = kw_data.setdefault("special_patterns", {})
                if not isinstance(sp, dict):
                    sp = {}
                    kw_data["special_patterns"] = sp
                wb = sp.setdefault("word_boundary", [])
                if not isinstance(wb, list):
                    wb = []
                    sp["word_boundary"] = wb
                if op == "add":
                    if token not in wb:
                        wb.append(token)
                else:
                    sp["word_boundary"] = [t for t in wb if str(t) != token]
                _write_json_file("keywords.json", kw_data)
                await hub.reload_all_from_disk()
                await reply_cmd_message(message, hub, f"OK — word_boundary updated")
                return True

            await reply_cmd_message(message, hub, f"Unknown command `{cmd}`. Try `{prefix}help`.")
            return True

        except ValueError as e:
            await reply_cmd_message(message, hub, f"Invalid number: {e}")
            return True
        except Exception as e:
            await reply_cmd_message(message, hub, f"Error: `{e}`")
            return True


def _env_truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _resolve_env_path() -> str | None:
    path = os.path.join(os.getcwd(), ".env")
    return path if os.path.isfile(path) else None


def _env_file_hash(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _spawn_restart_process() -> None:
    if getattr(sys, "frozen", False):
        args = [sys.executable]
    else:
        script = os.path.abspath(sys.argv[0])
        args = [sys.executable, script, *sys.argv[1:]]
    # os.execv on Windows splits unquoted paths with spaces (e.g. "D:\work miki\...").
    # Also: a plain Popen child dies with the parent console on os._exit — detach it.
    if os.name == "nt":
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        subprocess.Popen(
            args,
            cwd=os.getcwd(),
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
        )
        return
    try:
        os.execv(sys.executable, args)
    except OSError:
        subprocess.Popen(args, cwd=os.getcwd())


async def _restart_for_env_change(hub: ConfigHub) -> None:
    global _intentional_restart
    _intentional_restart = True
    print("[*] .env changed — restarting monitor...", flush=True)
    await hub.close_all_clients()
    await asyncio.sleep(0.5)
    _spawn_restart_process()
    os._exit(0)


async def _env_file_watch_loop(hub: ConfigHub) -> None:
    """Restart the process when .env content changes (tokens/webhooks need a full reload)."""
    if not _env_truthy(os.getenv("ENV_AUTO_RESTART"), default=True):
        return

    env_path = _resolve_env_path()
    if not env_path:
        return

    try:
        grace_s = float(os.getenv("ENV_AUTO_RESTART_GRACE_SECONDS", "15").strip() or "15")
    except ValueError:
        grace_s = 15.0
    grace_s = max(0.0, grace_s)
    if grace_s > 0:
        await asyncio.sleep(grace_s)

    try:
        poll_s = float(os.getenv("ENV_AUTO_RESTART_POLL_SECONDS", "2").strip() or "2")
    except ValueError:
        poll_s = 2.0
    poll_s = max(1.0, poll_s)

    last_hash = _env_file_hash(env_path)
    while True:
        await asyncio.sleep(poll_s)
        current = _env_file_hash(env_path)
        if current is None or last_hash is None:
            last_hash = current
            continue
        if current == last_hash:
            continue
        # Debounce: wait for the editor to finish writing the file.
        await asyncio.sleep(1.5)
        settled = _env_file_hash(env_path)
        if settled is None or settled == last_hash:
            last_hash = settled or last_hash
            continue
        await _restart_for_env_change(hub)


def install_exit_and_shutdown_hooks() -> None:
    """Ctrl+C, SIGTERM, console close (Windows), and process exit → offline webhook (best-effort)."""
    global _exit_hooks_installed
    if _exit_hooks_installed:
        return
    _exit_hooks_installed = True
    atexit.register(_send_shutdown_sync_best_effort)
    _register_windows_close_handler()

    def _sig(_signum, _frame):
        _send_shutdown_sync_best_effort()
        sys.exit(0)

    for _name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, _name):
            try:
                signal.signal(getattr(signal, _name), _sig)
            except Exception:
                pass

async def send_startup_test(webhook_url: str, tokens: list[dict] | None = None, keywords_config: dict | None = None):
    if not _env_truthy(os.getenv("STARTUP_TEST_WEBHOOK"), default=True):
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hostname = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or platform.node() or "unknown"
    python_ver = platform.python_version()
    os_name = platform.platform()
    account_names = [t.get("name", "Account") for t in (tokens or []) if isinstance(t, dict)]

    embed = {
        "title": "🔵 Monitor Online (Startup Test)",
        "description": "",
        "color": 0xED4245,  # match alert visibility
        "fields": [
            {"name": "Status", "value": "`ONLINE`", "inline": True},
            {"name": "Time (UTC)", "value": f"`{now}`", "inline": True},
            {"name": "Accounts", "value": f"`{len(account_names)}`", "inline": True},
            {"name": "Account Names", "value": ", ".join(account_names) if account_names else "*n/a*", "inline": False},
            {"name": "Runtime", "value": f"`{hostname}`\n`Python {python_ver}`\n`{os_name}`", "inline": False},
        ],
        "footer": {"text": "discord-tracker"},
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(webhook_url, json={"embeds": [embed]}) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                raise RuntimeError(f"Startup test webhook failed: HTTP {resp.status} {body[:500]}")


async def send_status_heartbeat(webhook_url: str, tokens: list[dict] | None = None):
    """Periodic proof-of-life to STATUS webhook — if these stop, the process is down."""
    if not _env_truthy(os.getenv("STATUS_HEARTBEAT_WEBHOOK"), default=True):
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hostname = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or platform.node() or "unknown"
    account_names = [t.get("name", "Account") for t in (tokens or []) if isinstance(t, dict)]
    interval_raw = os.getenv("STATUS_HEARTBEAT_SECONDS", "300").strip()
    try:
        interval_s = int(interval_raw)
    except ValueError:
        interval_s = 300

    embed = {
        "title": "🟢 Monitor heartbeat",
        "description": (
            "Process is alive. If you stop seeing this while the app should be running, "
            "the tracker is offline or stuck."
        ),
        "color": 0x57F287,
        "fields": [
            {"name": "Time (UTC)", "value": f"`{now}`", "inline": True},
            {"name": "Accounts", "value": f"`{len(account_names)}`", "inline": True},
            {"name": "Host", "value": f"`{hostname}`", "inline": True},
            {"name": "Interval", "value": f"`{interval_s}s` between heartbeats", "inline": False},
        ],
        "footer": {"text": "discord-tracker — e.g. no heartbeat for 10+ min ⇒ likely offline"},
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(webhook_url, json={"embeds": [embed]}) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                raise RuntimeError(f"Heartbeat webhook failed: HTTP {resp.status} {body[:500]}")


async def send_shutdown_log(webhook_url: str, tokens: list[dict] | None = None):
    if not _env_truthy(os.getenv("SHUTDOWN_LOG_WEBHOOK"), default=True):
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hostname = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or platform.node() or "unknown"
    python_ver = platform.python_version()
    os_name = platform.platform()
    account_names = [t.get("name", "Account") for t in (tokens or []) if isinstance(t, dict)]

    embed = {
        "title": "🔴 Monitor Offline (Shutdown)",
        "description": "",
        "color": 0x2B2D31,
        "fields": [
            {"name": "Status", "value": "`OFFLINE`", "inline": True},
            {"name": "Time (UTC)", "value": f"`{now}`", "inline": True},
            {"name": "Accounts", "value": f"`{len(account_names)}`", "inline": True},
            {"name": "Account Names", "value": ", ".join(account_names) if account_names else "*n/a*", "inline": False},
            {"name": "Runtime", "value": f"`{hostname}`\n`Python {python_ver}`\n`{os_name}`", "inline": False},
        ],
        "footer": {"text": "discord-tracker"},
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(webhook_url, json={"embeds": [embed]}) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                raise RuntimeError(f"Shutdown webhook failed: HTTP {resp.status} {body[:500]}")

def build_shutdown_embed_payload(tokens: list[dict] | None = None) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hostname = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or platform.node() or "unknown"
    python_ver = platform.python_version()
    os_name = platform.platform()
    account_names = [t.get("name", "Account") for t in (tokens or []) if isinstance(t, dict)]

    embed = {
        "title": "🔴 Monitor Offline (Shutdown)",
        "description": "",
        "color": 0x2B2D31,
        "fields": [
            {"name": "Status", "value": "`OFFLINE`", "inline": True},
            {"name": "Time (UTC)", "value": f"`{now}`", "inline": True},
            {"name": "Accounts", "value": f"`{len(account_names)}`", "inline": True},
            {"name": "Account Names", "value": ", ".join(account_names) if account_names else "*n/a*", "inline": False},
            {"name": "Runtime", "value": f"`{hostname}`\n`Python {python_ver}`\n`{os_name}`", "inline": False},
        ],
        "footer": {"text": "discord-tracker"},
    }
    return {"embeds": [embed]}

def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return []

def normalize_keywords_config(raw_keywords):
    """
    Supports two formats:
    - legacy: ["hiring", "remote", ...] (all treated as triggers)
    - structured: { triggers_primary: [...], tech_stack: [...], scoring: {...}, ... }
    """
    if isinstance(raw_keywords, list):
        legacy_triggers = [str(x) for x in raw_keywords if str(x).strip()]
        return {
            "triggers_primary": legacy_triggers,
            "triggers_freelance": [],
            "triggers_supply": [],
            "soft_triggers": [],
            "triggers_hidden_gold": [],
            "role_keywords": [],
            "tech_stack": [],
            "work_type_location": [],
            "budget_payment": [],
            "special_patterns": {"word_boundary": []},
            "scoring": {
                "trigger_primary": 10,
                "trigger_freelance": 12,
                "trigger_supply": 6,
                "trigger_hidden_gold": 14,
                "soft_trigger": 2,
                "role_keyword": 3,
                "tech_stack": 1,
                "work_type_location": 1,
                "budget_payment": 2,
                "min_score_to_alert_if_only_soft": 12,
                "min_score_to_alert": 1,
            },
        }

    if not isinstance(raw_keywords, dict):
        return normalize_keywords_config([])

    scoring = raw_keywords.get("scoring") if isinstance(raw_keywords.get("scoring"), dict) else {}
    special = raw_keywords.get("special_patterns") if isinstance(raw_keywords.get("special_patterns"), dict) else {}

    return {
        "triggers_primary": _as_list(raw_keywords.get("triggers_primary")),
        "triggers_freelance": _as_list(raw_keywords.get("triggers_freelance")),
        "triggers_supply": _as_list(raw_keywords.get("triggers_supply")),
        "soft_triggers": _as_list(raw_keywords.get("soft_triggers")),
        "triggers_hidden_gold": _as_list(raw_keywords.get("triggers_hidden_gold")),
        "role_keywords": _as_list(raw_keywords.get("role_keywords")),
        "tech_stack": _as_list(raw_keywords.get("tech_stack")),
        "work_type_location": _as_list(raw_keywords.get("work_type_location")),
        "budget_payment": _as_list(raw_keywords.get("budget_payment")),
        "special_patterns": {
            "word_boundary": _as_list(special.get("word_boundary")),
        },
        "scoring": {
            "trigger_primary": int(scoring.get("trigger_primary", 10)),
            "trigger_freelance": int(scoring.get("trigger_freelance", 12)),
            "trigger_supply": int(scoring.get("trigger_supply", 6)),
            "trigger_hidden_gold": int(scoring.get("trigger_hidden_gold", 14)),
            "soft_trigger": int(scoring.get("soft_trigger", 2)),
            "role_keyword": int(scoring.get("role_keyword", 3)),
            "tech_stack": int(scoring.get("tech_stack", 1)),
            "work_type_location": int(scoring.get("work_type_location", 1)),
            "budget_payment": int(scoring.get("budget_payment", 2)),
            "min_score_to_alert_if_only_soft": int(scoring.get("min_score_to_alert_if_only_soft", 12)),
            "min_score_to_alert": int(scoring.get("min_score_to_alert", 10)),
        },
    }

def find_matches(content: str, config: dict):
    content_lower = (content or "").lower()

    def match_substrings(keywords: list[str], *, min_len: int = 3) -> list[str]:
        hits = []
        for kw in keywords:
            k = kw.lower().strip()
            if not k:
                continue
            if len(k) < min_len:
                continue
            if k in content_lower:
                hits.append(kw)
        return hits

    # Avoid noisy matches on tiny substrings like "if"/"in"/"to".
    triggers_primary = match_substrings(config["triggers_primary"], min_len=3)
    triggers_freelance = match_substrings(config["triggers_freelance"], min_len=3)
    triggers_supply = match_substrings(config["triggers_supply"], min_len=3)
    soft_triggers = match_substrings(config["soft_triggers"], min_len=3)
    triggers_hidden = match_substrings(config["triggers_hidden_gold"], min_len=3)

    # Special cases: match whole words (e.g. "lf")
    boundary_hits = []
    for w in config["special_patterns"]["word_boundary"]:
        if re.search(rf"(?<!\\w){re.escape(w.lower())}(?!\\w)", content_lower):
            boundary_hits.append(w)

    role_hits = [kw for kw in config["role_keywords"] if kw.lower() in content_lower]
    tech_hits = [kw for kw in config["tech_stack"] if kw.lower() in content_lower]
    loc_hits = [kw for kw in config["work_type_location"] if kw.lower() in content_lower]
    pay_hits = [kw for kw in config["budget_payment"] if kw.lower() in content_lower]

    triggers = {
        "primary": triggers_primary,
        "freelance": triggers_freelance,
        "supply": triggers_supply,
        "soft": soft_triggers,
        "hidden_gold": triggers_hidden,
        "boundary": boundary_hits,
    }
    tags = {
        "roles": role_hits,
        "tech": tech_hits,
        "location": loc_hits,
        "payment": pay_hits,
    }

    s = config["scoring"]
    score = (
        len(triggers_primary) * s["trigger_primary"]
        + len(triggers_freelance) * s["trigger_freelance"]
        + len(triggers_supply) * s["trigger_supply"]
        + len(soft_triggers) * s["soft_trigger"]
        + len(triggers_hidden) * s["trigger_hidden_gold"]
        + len(boundary_hits) * s["trigger_primary"]
        + len(role_hits) * s["role_keyword"]
        + len(tech_hits) * s["tech_stack"]
        + len(loc_hits) * s["work_type_location"]
        + len(pay_hits) * s["budget_payment"]
    )

    return triggers, tags, score


OLLAMA_FILTER_SYSTEM = """You classify Discord messages for a developer job tracker.

ACCEPT only when ALL are true:
1) The author wants to HIRE / pay someone else (demand-side client), AND
2) The work is software development (developer, engineer, programmer, coder, bot, app,
   website, API, game code, script, fullstack, frontend, backend, etc.), AND
3) The message has real substance (role, stack, project, or pay) — not just a trigger phrase.

REJECT (relevant=false) when:
- Message is only vague phrases like "looking for", "hiring", "need help", "seeking" with
  no developer/development details
- Author is offering services / looking for a job for themselves (supply-side)
- Non-dev roles only (VA, caller, translator, marketer, designer-only, player LFG)
- Spam, scam, dating, empty chatter

Examples:
- "looking for" -> {"relevant": false, "reason": "too vague"}
- "looking for a React developer, $40/hr" -> {"relevant": true, "reason": "hiring react dev"}
- "I'm a developer looking for work" -> {"relevant": false, "reason": "supply-side"}

Reply with ONLY a single JSON object, no markdown:
{"relevant": true|false, "reason": "short"}"""


def is_too_vague_hiring_post(content: str) -> bool:
    """Cheap local gate: bare trigger phrases are not development tasks."""
    text = re.sub(r"\s+", " ", (content or "").strip())
    if not text:
        return True
    # Extremely short posts almost never contain a real job brief
    if len(text) < 16:
        return True

    low = text.lower()
    # Strip common trigger fluff and punctuation; require leftover substance
    fluff = [
        "looking for a",
        "looking for",
        "join our team",
        "paid project",
        "paid work",
        "need project",
        "have project",
        "need program",
        "need build",
        "need code",
        "help code",
        "help build",
        "need help",
        "need dev",
        "recruiting",
        "hiring",
        "seeking",
        "needed",
        "budget",
        "salary",
        "join us",
        "wts",
        "wtb",
    ]
    remainder = low
    for phrase in sorted(fluff, key=len, reverse=True):
        remainder = remainder.replace(phrase, " ")
    remainder = re.sub(r"[^a-z0-9+#.\s]", " ", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip()
    # Need leftover substance (e.g. "developer", "react", "fullstack") — not empty after fluff
    tokens = [t for t in remainder.split() if len(t) > 1]
    if not tokens:
        return True
    if len(tokens) == 1 and len(tokens[0]) < 4:
        return True
    return False


def ollama_filter_enabled() -> bool:
    load_dotenv()
    if not _env_truthy(os.getenv("OLLAMA_FILTER"), default=False):
        return False
    return bool((os.getenv("OLLAMA_API_KEY") or "").strip())


def _parse_ollama_relevant(raw: str) -> bool | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Strip optional ```json fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "relevant" in data:
            return bool(data["relevant"])
    except json.JSONDecodeError:
        pass
    # Fallback: look for a JSON object substring
    m = re.search(r"\{[^{}]*\}", text, flags=re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and "relevant" in data:
                return bool(data["relevant"])
        except json.JSONDecodeError:
            pass
    low = text.lower()
    if re.search(r'"relevant"\s*:\s*true', low):
        return True
    if re.search(r'"relevant"\s*:\s*false', low):
        return False
    return None


async def ollama_is_dev_hiring_post(content: str) -> tuple[bool, str]:
    """
    Stage-2 filter via Ollama Cloud (https://ollama.com).
    Returns (keep_alert, note). If filter is disabled, returns (True, "skipped").
    """
    load_dotenv()
    if not ollama_filter_enabled():
        return True, "skipped"

    api_key = (os.getenv("OLLAMA_API_KEY") or "").strip()
    base = (os.getenv("OLLAMA_BASE_URL") or "https://ollama.com").rstrip("/")
    model = (os.getenv("OLLAMA_MODEL") or "gemma4:31b-cloud").strip()
    # On API failure: allow (1) keeps alerts flowing; reject (0) drops the post.
    # Default reject so API failures don't leak vague keyword hits.
    allow_on_error = _env_truthy(os.getenv("OLLAMA_FILTER_ALLOW_ON_ERROR"), default=False)
    timeout_s = float(os.getenv("OLLAMA_TIMEOUT_SECONDS") or "45")

    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": OLLAMA_FILTER_SYSTEM},
            {
                "role": "user",
                "content": f"Classify this Discord message:\n\n{(content or '')[:2000]}",
            },
        ],
    }
    url = f"{base}/api/chat"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                raw_body = await resp.text()
                if resp.status != 200:
                    note = f"http_{resp.status}"
                    print(f"[OLLAMA] error {note}: {raw_body[:200]}", flush=True)
                    return allow_on_error, note
                data = json.loads(raw_body)
    except Exception as e:
        print(f"[OLLAMA] request failed: {e}", flush=True)
        return allow_on_error, f"error:{type(e).__name__}"

    # Ollama /api/chat -> message.content; OpenAI-compat would be choices[0]...
    reply = ""
    if isinstance(data, dict):
        msg = data.get("message") or {}
        if isinstance(msg, dict):
            reply = str(msg.get("content") or "")
        if not reply and isinstance(data.get("choices"), list) and data["choices"]:
            ch0 = data["choices"][0] or {}
            reply = str((ch0.get("message") or {}).get("content") or "")

    relevant = _parse_ollama_relevant(reply)
    if relevant is None:
        print(f"[OLLAMA] unparseable reply: {reply[:200]}", flush=True)
        return allow_on_error, "unparseable"
    return relevant, ("accept" if relevant else "reject")


def get_cmd_operator_token() -> str | None:
    """Dedicated token for admin commands (optional; see README)."""
    load_dotenv()
    raw = (os.getenv("CMD_OPERATOR_TOKEN") or os.getenv("CMD_OPREATOR_TOKEN") or "").strip()
    return raw or None


# guild_id -> invite URL (reuse so we don't spam create_invite)
_guild_invite_cache: dict[int, str] = {}


async def resolve_join_invite_url(message: discord.Message) -> str | None:
    """Vanity URL or a channel invite so outsiders can join and open the job post."""
    guild = message.guild
    if not guild:
        return None

    cached = _guild_invite_cache.get(guild.id)
    if cached:
        return cached

    vanity = getattr(guild, "vanity_url_code", None) or None
    if vanity:
        url = f"https://discord.gg/{vanity}"
        _guild_invite_cache[guild.id] = url
        return url

    channel = message.channel
    try:
        invite = await channel.create_invite(
            max_age=0,
            max_uses=0,
            unique=False,
            reason="discord-tracker job alert",
        )
        url = invite.url
        _guild_invite_cache[guild.id] = url
        return url
    except Exception as e:
        print(
            f"[WARN] could not create invite for guild={guild.id} "
            f"#{getattr(channel, 'name', '?')}: {e}",
            flush=True,
        )
        return None


def build_job_alert_components(*, message_url: str, join_url: str | None) -> list[dict]:
    """Link buttons (style 5) — work on webhooks without a bot interaction handler."""
    buttons: list[dict] = [
        {
            "type": 2,
            "style": 5,
            "label": "Show Message",
            "url": message_url,
        }
    ]
    if join_url:
        buttons.append(
            {
                "type": 2,
                "style": 5,
                "label": "Join Server",
                "url": join_url,
            }
        )
    return [{"type": 1, "components": buttons}]


async def send_job_alert_webhook(
    webhook_url: str,
    *,
    embed: discord.Embed,
    message_url: str,
    join_url: str | None,
) -> None:
    payload = {
        "embeds": [embed.to_dict()],
        "components": build_job_alert_components(
            message_url=message_url,
            join_url=join_url,
        ),
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                raise RuntimeError(f"Webhook HTTP {resp.status}: {body[:300]}")


class CommandOperatorClient(discord.Client):
    """Dedicated login: admin commands only (no job webhooks)."""

    def __init__(self, hub: ConfigHub):
        super().__init__()
        self.hub = hub

    async def on_ready(self):
        print(f"[OK] [CMD] command operator online | {self.user}", flush=True)
        if self.user and self.hub:
            self.hub.cmd_handler_user_id = self.user.id

    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        cmd_st = parse_cmd_env()
        if not cmd_st["enabled"]:
            return
        if cmd_st["channel_id"] and message.channel.id != cmd_st["channel_id"]:
            return
        if cmd_st["guild_id"] is not None and message.guild.id != cmd_st["guild_id"]:
            return
        hid = self.user.id if self.user else None
        if await process_admin_command(message, self.hub, handler_user_id=hid):
            return


class HiringMonitor(discord.Client):
    def __init__(
        self,
        account_name: str,
        keywords,
        jobs_webhook_url: str,
        blocklist: dict,
        hub: ConfigHub | None = None,
        handle_commands: bool = False,
    ):
        # `discord.py-self` doesn't expose `discord.Intents` like bot libraries do.
        super().__init__()
        self.account_name = account_name
        self.keywords = normalize_keywords_config(keywords)
        self.webhook_url = jobs_webhook_url
        self.blocklist = blocklist
        self.hub = hub
        self.handle_commands = bool(handle_commands)

    async def on_ready(self):
        print(f"[OK] {self.account_name} is online | {self.user}", flush=True)
        if self.handle_commands and self.hub and self.user:
            self.hub.cmd_handler_user_id = self.user.id

    async def on_message(self, message: discord.Message):
        # Only monitor servers
        if not message.guild:
            return

        cmd_st = parse_cmd_env()
        if cmd_st["enabled"] and self.handle_commands and self.hub is not None:
            hid = self.user.id if self.user else None
            if await process_admin_command(message, self.hub, handler_user_id=hid):
                return

        # Dedicated CMD_CHANNEL_ID: never emit job webhooks from this channel (any account).
        if (
            cmd_st["enabled"]
            and cmd_st["channel_id"]
            and message.channel.id == cmd_st["channel_id"]
        ):
            return

        # Same message can reach every monitor; skip job scan if it is clearly an admin command
        # from an authorized user handled on another client.
        if cmd_st["enabled"]:
            content = (message.content or "").strip()
            prefix = cmd_st["prefix"]
            if content.startswith(prefix):
                aid = message.author.id
                if aid in cmd_st["operators"]:
                    return
                if cmd_st["self_admin"] and self.hub and self.hub.cmd_handler_user_id == aid:
                    return

        # Ignore own messages by default (testing from the monitor account never alerts).
        # Set ALERT_ON_OWN_MESSAGES=1 in .env to allow self-tests.
        allow_own = _env_truthy(os.getenv("ALERT_ON_OWN_MESSAGES"), default=False)
        if message.author.id == self.user.id and not allow_own:
            return
        if message.author.bot:
            return

        if message_is_blocked(message, self.blocklist):
            return

        triggers, tags, score = find_matches(message.content, self.keywords)
        all_trigger_hits = (
            triggers["primary"]
            + triggers["freelance"]
            + triggers["hidden_gold"]
            + triggers["supply"]
            + triggers["boundary"]
        )
        any_soft = bool(triggers["soft"])
        if not all_trigger_hits and not any_soft:
            return

        if score < self.keywords["scoring"]["min_score_to_alert"]:
            return

        # If we only hit "soft" verbs/nouns (build/make/bot/app/etc), require higher score
        # so we don't alert on generic chatter without context.
        if not all_trigger_hits and any_soft and score < self.keywords["scoring"]["min_score_to_alert_if_only_soft"]:
            return

        preview = (message.content or "").replace("\n", " ")[:80]
        print(
            f"[HIT] {self.account_name} score={score} "
            f"#{getattr(message.channel, 'name', '?')} | {preview}",
            flush=True,
        )

        # Stage 1b: reject bare phrases like "looking for" with no job details
        if is_too_vague_hiring_post(message.content or ""):
            print(
                f"[SKIP] too vague | #{getattr(message.channel, 'name', '?')} | {preview}",
                flush=True,
            )
            return

        # Stage 2: Ollama Cloud — keep only developer hiring / development tasks
        keep, ollama_note = await ollama_is_dev_hiring_post(message.content or "")
        if ollama_note != "skipped":
            print(
                f"[OLLAMA] {ollama_note} | #{getattr(message.channel, 'name', '?')} | {preview}",
                flush=True,
            )
        if not keep:
            return

        # Highlight trigger keywords (exact ❗keyword❗).
        # Note: Discord embeds don't support coloring just the "!" characters.
        highlighted_content = message.content
        for kw in (all_trigger_hits + triggers["soft"]):
            highlighted_content = re.sub(
                re.escape(kw),
                f"❗{kw}❗",
                highlighted_content,
                flags=re.IGNORECASE
            )

        # Modern & professional embed
        embed = discord.Embed(
            title="🚀 New Hiring Opportunity Detected",
            description="",
            color=0xED4245,  # Discord red accent for visibility
            timestamp=message.created_at
        )

        # Posted by: plain text (no profile link — avoids click-to-profile)
        embed.set_author(
            name=f"{message.author.display_name} -> @{message.author.name} -> [{message.author.id}]",
            icon_url=message.author.avatar.url if message.author.avatar else None,
        )

        embed.add_field(
            name="Server",
            value=f"{message.guild.name} -> [{message.guild.id}]",
            inline=True,
        )
        embed.add_field(name="Channel", value=f"#{message.channel.name}", inline=True)
        safe_detector = discord.utils.escape_markdown(self.account_name)
        embed.add_field(name="Detected by", value=f"**{safe_detector}**", inline=True)

        # Server icon on top-right
        if message.guild and getattr(message.guild, "icon", None):
            try:
                if message.guild.icon and message.guild.icon.url:
                    embed.set_thumbnail(url=message.guild.icon.url)
            except Exception:
                pass

        tag_lines = []
        if tags["roles"]:
            tag_lines.append(f"Roles: {', '.join(tags['roles'])}")
        if tags["tech"]:
            tag_lines.append(f"Tech: {', '.join(tags['tech'])}")
        if tags["location"]:
            tag_lines.append(f"Location/Type: {', '.join(tags['location'])}")
        if tags["payment"]:
            tag_lines.append(f"Budget/Pay: {', '.join(tags['payment'])}")

        if tag_lines:
            embed.add_field(name="Tags", value="\n".join(tag_lines), inline=False)

        # Message content (only shown once)
        embed.add_field(
            name="Message Content",
            value=f"```{highlighted_content}```" if highlighted_content else "*No content*",
            inline=False
        )

        join_url = await resolve_join_invite_url(message)
        action_bits = [f"[Show Message]({message.jump_url})"]
        if join_url:
            action_bits.append(f"[Join Server]({join_url})")
        else:
            action_bits.append("_Join invite unavailable (need Create Invite permission)_")
        embed.add_field(name="Actions", value=" · ".join(action_bits), inline=False)

        # Footer avatar only (footer text does not support **bold**)
        detector_avatar = None
        try:
            if self.user and self.user.avatar and self.user.avatar.url:
                detector_avatar = self.user.avatar.url
        except Exception:
            detector_avatar = None

        embed.set_footer(text="\u200b", icon_url=detector_avatar)

        # Per detection / per logged-in account: wait 3–6s (random) before hitting the webhook
        await asyncio.sleep(random.uniform(3.0, 6.0))

        # Send to your webhook (link buttons under the embed)
        try:
            await send_job_alert_webhook(
                self.webhook_url,
                embed=embed,
                message_url=message.jump_url,
                join_url=join_url,
            )
            print(
                f"[SENT] job alert -> webhook ({self.account_name})"
                + (f" invite=ok" if join_url else " invite=missing"),
                flush=True,
            )
        except Exception as e:
            print(f"[ERR] webhook send failed ({self.account_name}): {e}", flush=True)

async def main():
    tokens, keywords, jobs_webhook_url, status_webhook_url = load_config()
    blocklist = load_blocklist()
    hub = ConfigHub(status_webhook_url)
    cmd_st = parse_cmd_env()
    handler_name = os.getenv("CMD_HANDLER_ACCOUNT_NAME", "").strip()

    # Shutdown / offline webhook: refresh payload on each attempt; hooks for Ctrl+C, console X, atexit
    install_exit_and_shutdown_hooks()

    # Post a quick webhook ping on every process start (can disable via STARTUP_TEST_WEBHOOK=0)
    await send_startup_test(status_webhook_url, tokens=tokens, keywords_config=keywords)

    op_token = get_cmd_operator_token()
    tasks = []

    # Which monitor session runs !commands: dedicated CMD_OPERATOR_TOKEN, else optional name, else first token.
    cmd_token_index: int | None = None
    if cmd_st["enabled"] and op_token:
        for j, td in enumerate(tokens):
            if str(td.get("token", "")).strip() == op_token:
                cmd_token_index = j
                break

    standalone_cmd_client = cmd_st["enabled"] and op_token is not None and cmd_token_index is None

    for i, token_data in enumerate(tokens):
        is_cmd_handler = False
        if cmd_st["enabled"] and not standalone_cmd_client:
            if op_token:
                is_cmd_handler = i == cmd_token_index
            elif handler_name:
                is_cmd_handler = token_data.get("name") == handler_name
            else:
                is_cmd_handler = i == 0
        client = HiringMonitor(
            account_name=token_data["name"],
            keywords=keywords,
            jobs_webhook_url=jobs_webhook_url,
            blocklist=blocklist,
            hub=hub,
            handle_commands=is_cmd_handler,
        )
        hub.clients.append(client)
        hub.register_client(client)
        tasks.append(client.start(token_data["token"]))

    if standalone_cmd_client:
        cmd_client = CommandOperatorClient(hub)
        hub.register_client(cmd_client)
        tasks.append(cmd_client.start(op_token))

    print("Starting all self-bots...", flush=True)

    hb_task = None
    try:
        hb_sec = int(os.getenv("STATUS_HEARTBEAT_SECONDS", "300").strip() or "300")
    except ValueError:
        hb_sec = 300
    if _env_truthy(os.getenv("STATUS_HEARTBEAT_WEBHOOK"), default=True) and hb_sec > 0:

        async def _heartbeat_loop():
            # Send soon after start, then every hb_sec (previously we slept first, so nothing
            # posted for a full interval — easy to miss "offline" for a long time).
            while True:
                try:
                    load_dotenv()
                    t, _kw, _j, su = load_config()
                    await send_status_heartbeat(su, t)
                except Exception:
                    pass
                await asyncio.sleep(hb_sec)

        hb_task = asyncio.create_task(_heartbeat_loop())

    env_watch_task = None
    if _env_truthy(os.getenv("ENV_AUTO_RESTART"), default=True) and _resolve_env_path():
        env_watch_task = asyncio.create_task(_env_file_watch_loop(hub))

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if env_watch_task is not None:
            env_watch_task.cancel()
            try:
                await env_watch_task
            except asyncio.CancelledError:
                pass
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
        _send_shutdown_sync_best_effort()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        if not _intentional_restart:
            try:
                _send_shutdown_sync_best_effort()
            except Exception:
                pass
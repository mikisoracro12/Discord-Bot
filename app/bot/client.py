import logging
import discord

from app.classifier.rule_engine import RuleEngine
from app.database.database import get_session
from app.database.repository import Repository
from app.extractor.budget import extract_budget
from app.bot.notifier import send_task

log = logging.getLogger("discord-task-finder")

class TaskFinderBot(discord.Client):
    def __init__(self, settings):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True

        super().__init__(intents=intents)
        self.settings = settings
        self.engine = RuleEngine(
            threshold=settings.task_threshold,
            high_threshold=settings.high_confidence_threshold,
        )

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user, self.user.id)
        log.info("Monitoring %d configured channels", len(self.settings.source_channels))

    async def on_message(self, message: discord.Message):
        try:
            if message.author.bot:
                return

            if message.channel.id not in self.settings.source_channels:
                return

            if not message.content or not message.content.strip():
                return

            async with await get_session() as session:
                repo = Repository(session)

                if await repo.is_processed(message.id):
                    return

                result = self.engine.classify(message.content)

                # Mark every inspected source message so restarts don't repeatedly inspect it.
                await repo.mark_processed(message)

                log.info(
                    "Checked message %s | score=%s | accepted=%s | category=%s",
                    message.id, result.score, result.accepted, result.category
                )

                if not result.accepted:
                    return

                destination = self.get_channel(self.settings.destination_channel_id)
                if destination is None:
                    log.error("Destination channel %s not found", self.settings.destination_channel_id)
                    return

                budget = extract_budget(message.content)
                await send_task(destination, message, result, budget)
                await repo.save_task(message, result, budget)

        except Exception:
            log.exception("Error processing message %s", getattr(message, "id", "unknown"))

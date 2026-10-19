import asyncio
import logging

from app.config.settings import get_settings
from app.bot.client import TaskFinderBot
from app.database.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

async def main():
    settings = get_settings()
    await init_db(settings.database_url)

    bot = TaskFinderBot(settings)
    await bot.start(settings.discord_bot_token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

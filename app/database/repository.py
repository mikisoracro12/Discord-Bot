import json
from sqlalchemy import select
from app.database.models import ProcessedMessage, DetectedTask

class Repository:
    def __init__(self, session):
        self.session = session

    async def is_processed(self, message_id: int) -> bool:
        result = await self.session.execute(
            select(ProcessedMessage).where(ProcessedMessage.message_id == message_id)
        )
        return result.scalar_one_or_none() is not None

    async def mark_processed(self, message):
        self.session.add(ProcessedMessage(
            server_id=message.guild.id if message.guild else 0,
            channel_id=message.channel.id,
            message_id=message.id,
        ))
        await self.session.commit()

    async def save_task(self, message, result, budget):
        task = DetectedTask(
            server_id=message.guild.id if message.guild else 0,
            channel_id=message.channel.id,
            message_id=message.id,
            author_id=message.author.id,
            content=message.content,
            category=result.category,
            technologies=json.dumps(result.technologies),
            budget=budget,
            score=result.score,
            confidence=result.confidence,
            message_url=message.jump_url,
        )
        self.session.add(task)
        await self.session.commit()

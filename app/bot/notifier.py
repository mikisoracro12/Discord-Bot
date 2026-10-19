import discord
from app.extractor.category import human_category

async def send_task(channel: discord.TextChannel, message, result, budget):
    embed = discord.Embed(
        title="🚨 New Software Task",
        description=message.content[:4000],
        color=discord.Color.green(),
        url=message.jump_url,
    )

    embed.add_field(
        name="Category",
        value=human_category(result.category),
        inline=True,
    )
    embed.add_field(
        name="Confidence",
        value=f"{result.score}% ({result.confidence})",
        inline=True,
    )
    embed.add_field(
        name="Budget",
        value=budget or "Not specified",
        inline=True,
    )
    embed.add_field(
        name="Technologies",
        value=", ".join(result.technologies) or "Not specified",
        inline=False,
    )
    embed.add_field(
        name="Source",
        value=f"{message.guild.name} → #{message.channel.name}",
        inline=False,
    )
    embed.add_field(
        name="Posted by",
        value=f"{message.author} (`{message.author.id}`)",
        inline=False,
    )
    embed.add_field(
        name="Original Message",
        value=f"[Jump to message]({message.jump_url})",
        inline=False,
    )
    embed.set_footer(text="Discord Task Finder • Rule-based V1")

    await channel.send(embed=embed)

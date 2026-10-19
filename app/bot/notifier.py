import discord
from app.extractor.category import human_category

async def send_task(channel: discord.TextChannel, message, result, budget):
    via = "AI" if getattr(result, "classifier", "") == "ai" else "Rules"
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
    if result.reason:
        embed.add_field(
            name="Why matched",
            value=result.reason[:1024],
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
    embed.set_footer(text=f"Discord Task Finder • {via}")

    await channel.send(embed=embed)

import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print("--------------------------------")
    print("🥊 OSBL FIGHT COMMISSIONER ONLINE")
    print(f"Logged in as: {bot.user}")
    print("--------------------------------")


@bot.command()
async def osbl(ctx):
    await ctx.send(
        "🥊 **OSBL FIGHT COMMISSIONER** 🥊\n"
        "Commissioner systems are ONLINE.\n\n"
        "🏆 ONESTATE BOXING LEAGUE\n"
        "📊 Rankings • RP • Records • Gyms • Championships"
    )


token = os.getenv("DISCORD_TOKEN")

if not token:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing.")

bot.run(token)

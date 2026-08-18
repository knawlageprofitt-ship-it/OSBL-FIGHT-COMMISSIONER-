
import os
import discord
import asyncpg
from discord.ext import commands


# =========================================================
# OSBL CONFIGURATION
# =========================================================

intents = discord.Intents.default()
intents.message_content = True

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")


# =========================================================
# OSBL BOT
# =========================================================

class OSBLBot(commands.Bot):
    async def setup_hook(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable is missing.")

        self.db = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1,
            max_size=5
        )

        async with self.db.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fighters (
                    id BIGSERIAL PRIMARY KEY,
                    fighter_key TEXT UNIQUE NOT NULL,
                    fighter_name TEXT NOT NULL,
                    division TEXT NOT NULL,
                    gym TEXT NOT NULL,

                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    rp INTEGER NOT NULL DEFAULT 0,

                    progression_rank TEXT NOT NULL DEFAULT 'Prospect',
                    division_rank INTEGER,

                    career_earnings BIGINT NOT NULL DEFAULT 0,

                    champion BOOLEAN NOT NULL DEFAULT FALSE,
                    title_defenses INTEGER NOT NULL DEFAULT 0,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

        print("✅ OSBL Fighter Database Ready")

    async def close(self):
        if hasattr(self, "db"):
            await self.db.close()

        await super().close()


bot = OSBLBot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# =========================================================
# READY EVENT
# =========================================================

@bot.event
async def on_ready():
    print("--------------------------------")
    print("🥊 OSBL FIGHT COMMISSIONER ONLINE")
    print(f"Logged in as: {bot.user}")
    print("📊 Fighter Database Connected")
    print("--------------------------------")


# =========================================================
# SYSTEM STATUS
# =========================================================

@bot.command()
async def osbl(ctx):
    embed = discord.Embed(
        title="🥊 OSBL FIGHT COMMISSIONER",
        description="Commissioner systems are **ONLINE**.",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="🏆 ONESTATE BOXING LEAGUE",
        value="Rankings • RP • Records • Gyms • Championships",
        inline=False
    )

    embed.add_field(
        name="🟢 DATABASE",
        value="Fighter Registry Online",
        inline=False
    )

    await ctx.send(embed=embed)


# =========================================================
# REGISTER FIGHTER
# FORMAT:
# !register Fighter Name | Division | Gym
# =========================================================

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIALS")
async def register(ctx, *, details: str = None):

    if not details:
        await ctx.send(
            "❌ **REGISTRATION FORMAT**\n"
            "`!register Fighter Name | Division | Gym`\n\n"
            "Example:\n"
            "`!register Baybe Mama | Lightweight | RADEEMERS`"
        )
        return

    parts = [part.strip() for part in details.split("|")]

    if len(parts) != 3:
        await ctx.send(
            "❌ Use exactly this format:\n"
            "`!register Fighter Name | Division | Gym`"
        )
        return

    fighter_name, division, gym = parts

    divisions = {
        "lightweight": "Lightweight",
        "middleweight": "Middleweight",
        "heavyweight": "Heavyweight"
    }

    division_key = division.lower()

    if division_key not in divisions:
        await ctx.send(
            "❌ Division must be:\n"
            "**Lightweight, Middleweight, or Heavyweight**"
        )
        return

    division = divisions[division_key]
    fighter_key = fighter_name.casefold()

    try:
        await bot.db.execute(
            """
            INSERT INTO fighters (
                fighter_key,
                fighter_name,
                division,
                gym
            )
            VALUES ($1, $2, $3, $4)
            """,
            fighter_key,
            fighter_name,
            division,
            gym
        )

    except asyncpg.UniqueViolationError:
        await ctx.send(
            f"⚠️ **{fighter_name} is already registered in OSBL.**"
        )
        return

    embed = discord.Embed(
        title="🥊 OSBL FIGHTER REGISTRATION",
        description="✅ **FIGHTER OFFICIALLY REGISTERED**",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="🥊 Fighter",
        value=fighter_name,
        inline=False
    )

    embed.add_field(
        name="⚖️ Division",
        value=division,
        inline=True
    )

    embed.add_field(
        name="🏢 Gym / Promotion",
        value=gym,
        inline=True
    )

    embed.add_field(
        name="📊 Starting Record",
        value="0-0",
        inline=True
    )

    embed.add_field(
        name="💠 Starting RP",
        value="0 RP",
        inline=True
    )

    embed.add_field(
        name="🥉 Progression",
        value="Prospect",
        inline=True
    )

    embed.set_footer(
        text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    await ctx.send(embed=embed)


# =========================================================
# FIGHTER PROFILE
# FORMAT:
# !fighter Fighter Name
# =========================================================

@bot.command()
async def fighter(ctx, *, fighter_name: str = None):

    if not fighter_name:
        await ctx.send(
            "❌ Use:\n"
            "`!fighter Fighter Name`"
        )
        return

    fighter_key = fighter_name.casefold()

    row = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE fighter_key = $1
        """,
        fighter_key
    )

    if not row:
        await ctx.send(
            f"❌ **{fighter_name} is not registered in OSBL.**"
        )
        return

    title_status = (
        "👑 CHAMPION"
        if row["champion"]
        else "🥊 ACTIVE FIGHTER"
    )

    division_rank = (
        f"#{row['division_rank']}"
        if row["division_rank"]
        else "Unranked"
    )

    embed = discord.Embed(
        title="🥊 OSBL OFFICIAL FIGHTER PROFILE",
        description=f"**{row['fighter_name']}**",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="🏢 Gym",
        value=row["gym"],
        inline=True
    )

    embed.add_field(
        name="⚖️ Division",
        value=row["division"],
        inline=True
    )

    embed.add_field(
        name="🥊 Record",
        value=f"{row['wins']}-{row['losses']}",
        inline=True
    )

    embed.add_field(
        name="💠 RP",
        value=f"{row['rp']} RP",
        inline=True
    )

    embed.add_field(
        name="📈 Progression",
        value=row["progression_rank"],
        inline=True
    )

    embed.add_field(
        name="🏅 Division Ranking",
        value=division_rank,
        inline=True
    )

    embed.add_field(
        name="💰 Career Earnings",
        value=f"${row['career_earnings']:,}",
        inline=True
    )

    embed.add_field(
        name="👑 Status",
        value=title_status,
        inline=True
    )

    embed.add_field(
        name="🛡️ Title Defenses",
        value=str(row["title_defenses"]),
        inline=True
    )

    embed.set_footer(
        text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    await ctx.send(embed=embed)


# =========================================================
# OSBL ROSTER
# FORMAT:
# !fighters
# OR
# !fighters Lightweight
# =========================================================

@bot.command()
async def fighters(ctx, *, division: str = None):

    if division:

        divisions = {
            "lightweight": "Lightweight",
            "middleweight": "Middleweight",
            "heavyweight": "Heavyweight"
        }

        division_key = division.lower()

        if division_key not in divisions:
            await ctx.send(
                "❌ Division must be:\n"
                "**Lightweight, Middleweight, or Heavyweight**"
            )
            return

        official_division = divisions[division_key]

        rows = await bot.db.fetch(
            """
            SELECT *
            FROM fighters
            WHERE division = $1
            ORDER BY rp DESC, wins DESC, fighter_name ASC
            LIMIT 25
            """,
            official_division
        )

        title = f"🥊 OSBL {official_division.upper()} ROSTER"

    else:

        rows = await bot.db.fetch(
            """
            SELECT *
            FROM fighters
            ORDER BY division, rp DESC, wins DESC, fighter_name ASC
            LIMIT 25
            """
        )

        title = "🥊 OSBL OFFICIAL FIGHTER ROSTER"

    if not rows:
        await ctx.send(
            "📋 No fighters are registered yet."
        )
        return

    roster_lines = []

    for row in rows:
        roster_lines.append(
            f"🥊 **{row['fighter_name']}** "
            f"— {row['division']} "
            f"— {row['gym']} "
            f"— {row['wins']}-{row['losses']} "
            f"— {row['rp']} RP"
        )

    embed = discord.Embed(
        title=title,
        description="\n".join(roster_lines),
        color=discord.Color.gold()
    )

    embed.set_footer(
        text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    await ctx.send(embed=embed)


# =========================================================
# COMMAND ERROR HANDLING
# =========================================================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingAnyRole):
        await ctx.send(
            "⛔ **OSBL STAFF AUTHORIZATION REQUIRED**\n"
            "Only the OSBL Commissioner or OSBL Officials "
            "may use that command."
        )
        return

    print(f"Command error: {error}")

    await ctx.send(
        "⚠️ An OSBL Commissioner system error occurred."
    )


# =========================================================
# START BOT
# =========================================================

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing."
    )

bot.run(DISCORD_TOKEN)

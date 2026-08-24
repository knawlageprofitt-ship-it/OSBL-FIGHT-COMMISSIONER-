
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

# ============================================================
# RECORD FIGHT RESULT
# FORMAT:
# !result Winner | Loser | Score
# Example:
# !result Baybe Mama | Kayoski | 2-1
# ============================================================

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def result(ctx, *, details: str = None):

    if not details:
        await ctx.send(
            "❌ **FIGHT RESULT FORMAT**\n"
            "`!result Winner | Loser | Score`\n"
            "Example:\n"
            "`!result Baybe Mama | Kayoski | 2-1`"
        )
        return
    parts = [part.strip() for part in details.split("|")]

    if len(parts) != 3:
        await ctx.send(
            "❌ Use exactly this format:\n"
            "`!result Winner | Loser | Score`"
        )
        return

    winner_name, loser_name, score = parts

    winner_key = winner_name.casefold()
    loser_key = loser_name.casefold()
    if score not in ("2-0", "2-1"):
        await ctx.send(
            "❌ Regular fight score must be **2-0** or **2-1**."
        )
        return

    winner = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE fighter_key = $1
        """,
        winner_key
    )

    loser = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE fighter_key = $1
        """,
        loser_key
    )

    if not winner:
        await ctx.send(
            f"❌ **{winner_name}** is not registered in OSBL."
        )
        return

    if not loser:
        await ctx.send(
            f"❌ **{loser_name}** is not registered in OSBL."
        )
        return

    if winner["division"] != loser["division"]:
        await ctx.send(
            "❌ Fighters must be in the same division."
        )
        return

    winner_rp_gain = 10
    loser_rp_gain = 2
    bonuses = []

    if score == "2-0":
        winner_rp_gain += 3
        bonuses.append("🧹 2-0 Sweep: +3 RP")

    opponent_rank = loser["division_rank"]

    if opponent_rank is not None:
        if opponent_rank <= 5:
            winner_rp_gain += 8
            bonuses.append("🔥 Beat Top-5 Opponent: +8 RP")
        elif opponent_rank <= 10:
            winner_rp_gain += 5
            bonuses.append("⭐ Beat Top-10 Opponent: +5 RP")
    new_winner_rp = winner["rp"] + winner_rp_gain
    new_loser_rp = loser["rp"] + loser_rp_gain

    def progression_for(rp):
        if rp >= 140:
            return "#1 Contender"
        elif rp >= 110:
            return "Elite Contender"
        elif rp >= 80:
            return "Top Contender"
        elif rp >= 50:
            return "Contender"
        elif rp >= 25:
            return "Rising Prospect"
        else:
            return "Prospect"

    winner_progression = progression_for(new_winner_rp)
    loser_progression = progression_for(new_loser_rp)

    async with bot.db.acquire() as conn:
        async with conn.transaction():

            await conn.execute(
                """
                UPDATE fighters
                SET wins = wins + 1,
                    rp = $1,
                    progression_rank = $2,
                    updated_at = NOW()
                WHERE fighter_key = $3
                """,
                new_winner_rp,
                winner_progression,
                winner_key
            )

            await conn.execute(
                """
                UPDATE fighters
                SET losses = losses + 1,
                    rp = $1,
                    progression_rank = $2,
                    updated_at = NOW()
                WHERE fighter_key = $3
                """,
                new_loser_rp,
                loser_progression,
                loser_key
            )
        bonuses_text = "\n".join(bonuses) if bonuses else "None"

    embed = discord.Embed(
        title="🥊 OSBL OFFICIAL FIGHT RESULT",
        description=f"**{winner['division']} Division**",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="🏆 Winner",
        value=(
            f"**{winner['fighter_name']}**\n"
            f"Score: **{score}**\n"
            f"Record: **{winner['wins'] + 1}-{winner['losses']}**\n"
            f"RP: **{new_winner_rp}** (+{winner_rp_gain})\n"
            f"Progression: **{winner_progression}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🥊 Opponent",
        value=(
            f"**{loser['fighter_name']}**\n"
            f"Record: **{loser['wins']}-{loser['losses'] + 1}**\n"
            f"RP: **{new_loser_rp}** (+{loser_rp_gain})\n"
            f"Progression: **{loser_progression}**"
        ),
        inline=False
    )

    embed.add_field(
        name="⭐ RP Bonuses",
        value=bonuses_text,
        inline=False
    )

    embed.set_footer(
        text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    await ctx.send(embed=embed)   

# ============================================
# SET DIVISION RANK
# FORMAT:
# !setrank Fighter Name | Rank
# ============================================

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def setrank(ctx, *, details: str = None):

    if not details:
        await ctx.send(
            "❌ **SET RANK FORMAT**\n"
            "`!setrank Fighter Name | Rank`\n"
            "Example:\n"
            "`!setrank Test Fighter Four | 5`"
        )
        return

    parts = [part.strip() for part in details.split("|")]

    if len(parts) != 2:
        await ctx.send(
            "❌ Use exactly this format:\n"
            "`!setrank Fighter Name | Rank`"
        )
        return

    fighter_name, rank_text = parts
    fighter_key = fighter_name.casefold()

    try:
        rank = int(rank_text)
    except ValueError:
        await ctx.send("❌ Rank must be a number.")
        return

    if rank < 1 or rank > 25:
        await ctx.send("❌ Rank must be between 1 and 25.")
        return

    fighter = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE fighter_key = $1
        """,
        fighter_key
    )

    if not fighter:
        await ctx.send(
            f"❌ **{fighter_name}** is not registered in OSBL."
        )
        return

    await bot.db.execute(
        """
        UPDATE fighters
        SET division_rank = $1,
            updated_at = NOW()
        WHERE fighter_key = $2
        """,
        rank,
        fighter_key
    )

    embed = discord.Embed(
        title="🥊 OSBL DIVISION RANK UPDATED",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="🥊 Fighter",
        value=fighter["fighter_name"],
        inline=False
    )

    embed.add_field(
        name="⚖️ Division",
        value=fighter["division"],
        inline=True
    )

    embed.add_field(
        name="🏅 New Ranking",
        value=f"#{rank}",
        inline=True
    )

    embed.set_footer(
        text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    await ctx.send(embed=embed)

# ============================================
# RECORD CHAMPIONSHIP RESULT
# FORMAT:
# !champresult Winner | Loser | Score
# ============================================

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def champresult(ctx, *, details: str = None):

    if not details:
        await ctx.send(
            "❌ **CHAMPIONSHIP RESULT FORMAT**\n"
            "`!champresult Winner | Loser | Score`\n"
            "Example:\n"
            "`!champresult Test Champ One | Test Champ Two | 3-1`"
        )
        return

    parts = [part.strip() for part in details.split("|")]

    if len(parts) != 3:
        await ctx.send(
            "❌ Use exactly this format:\n"
            "`!champresult Winner | Loser | Score`"
        )
        return

    winner_name, loser_name, score = parts

    if score not in ("3-0", "3-1", "3-2"):
        await ctx.send(
            "❌ Championship score must be **3-0, 3-1, or 3-2**."
        )
        return

    winner_key = winner_name.casefold()
    loser_key = loser_name.casefold()

    winner = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE fighter_key = $1
        """,
        winner_key
    )

    loser = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE fighter_key = $1
        """,
        loser_key
    )

    if not winner:
        await ctx.send(f"❌ **{winner_name}** is not registered in OSBL.")
        return

    if not loser:
        await ctx.send(f"❌ **{loser_name}** is not registered in OSBL.")
        return

    if winner["division"] != loser["division"]:
        await ctx.send("❌ Fighters must be in the same division.")
        return

    winner_rp_gain = 20
    loser_rp_gain = 5
    bonuses = []

    if score == "3-0":
        winner_rp_gain += 5
        bonuses.append("🧹 3-0 Championship Sweep: +5 RP")

    defending_champion = bool(winner["champion"])

    if defending_champion:
        winner_rp_gain += 15
        bonuses.append("🛡️ Successful Title Defense: +15 RP")

    new_winner_rp = winner["rp"] + winner_rp_gain
    new_loser_rp = loser["rp"] + loser_rp_gain

    def progression_for(rp):
        if rp >= 140:
            return "#1 Contender"
        elif rp >= 110:
            return "Elite Contender"
        elif rp >= 80:
            return "Top Contender"
        elif rp >= 50:
            return "Contender"
        elif rp >= 25:
            return "Rising Prospect"
        else:
            return "Prospect"

    winner_progression = progression_for(new_winner_rp)
    loser_progression = progression_for(new_loser_rp)

    async with bot.db.acquire() as conn:
        async with conn.transaction():

            await conn.execute(
                """
                UPDATE fighters
                SET wins = wins + 1,
                    rp = $1,
                    progression_rank = $2,
                    champion = TRUE,
                    title_defenses = title_defenses + $3,
                    updated_at = NOW()
                WHERE fighter_key = $4
                """,
                new_winner_rp,
                winner_progression,
                1 if defending_champion else 0,
                winner_key
            )

            await conn.execute(
                """
                UPDATE fighters
                SET losses = losses + 1,
                    rp = $1,
                    progression_rank = $2,
                    champion = FALSE,
                    updated_at = NOW()
                WHERE fighter_key = $3
                """,
                new_loser_rp,
                loser_progression,
                loser_key
            )

    bonuses_text = "\n".join(bonuses) if bonuses else "None"

    embed = discord.Embed(
        title="🏆 OSBL OFFICIAL CHAMPIONSHIP RESULT",
        description=f"**{winner['division']} Championship**",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="👑 Champion",
        value=(
            f"**{winner['fighter_name']}**\n"
            f"Score: **{score}**\n"
            f"Record: **{winner['wins'] + 1}-{winner['losses']}**\n"
            f"RP: **{new_winner_rp}** (+{winner_rp_gain})\n"
            f"Progression: **{winner_progression}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🥊 Opponent",
        value=(
            f"**{loser['fighter_name']}**\n"
            f"Record: **{loser['wins']}-{loser['losses'] + 1}**\n"
            f"RP: **{new_loser_rp}** (+{loser_rp_gain})\n"
            f"Progression: **{loser_progression}**"
        ),
        inline=False
    )

    embed.add_field(
        name="⭐ Championship Bonuses",
        value=bonuses_text,
        inline=False
    )

    embed.set_footer(
        text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    await ctx.send(embed=embed)

# ============================================
# CURRENT OSBL CHAMPIONS
# FORMAT:
# !champions
# ============================================

@bot.command()
async def champions(ctx):

    divisions = ["Lightweight", "Middleweight", "Heavyweight"]

    embed = discord.Embed(
        title="🏆 OSBL CURRENT CHAMPIONS",
        description="Official ONESTATE Boxing League titleholders",
        color=discord.Color.gold()
    )

    for division in divisions:

        champion = await bot.db.fetchrow(
            """
            SELECT *
            FROM fighters
            WHERE division = $1
              AND champion = TRUE
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            division
        )

        if champion:
            ranking = (
                f"#{champion['division_rank']}"
                if champion["division_rank"]
                else "Unranked"
            )

            embed.add_field(
                name=f"👑 {division} Champion",
                value=(
                    f"**{champion['fighter_name']}**\n"
                    f"Gym: **{champion['gym']}**\n"
                    f"Record: **{champion['wins']}-{champion['losses']}**\n"
                    f"RP: **{champion['rp']}**\n"
                    f"Progression: **{champion['progression_rank']}**\n"
                    f"Division Ranking: **{ranking}**\n"
                    f"Title Defenses: **{champion['title_defenses']}**"
                ),
                inline=False
            )

        else:
            embed.add_field(
                name=f"👑 {division} Champion",
                value="**VACANT**",
                inline=False
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

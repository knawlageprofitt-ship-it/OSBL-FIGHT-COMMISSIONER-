
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

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fight_history (
                    id BIGSERIAL PRIMARY KEY,
                    fight_type TEXT NOT NULL,
                    winner_key TEXT NOT NULL,
                    loser_key TEXT NOT NULL,
                    score TEXT NOT NULL,

                    winner_rp_before INTEGER NOT NULL,
                    loser_rp_before INTEGER NOT NULL,

                    winner_wins_before INTEGER NOT NULL,
                    winner_losses_before INTEGER NOT NULL,
                    loser_wins_before INTEGER NOT NULL,
                    loser_losses_before INTEGER NOT NULL,

                    winner_earnings_before BIGINT NOT NULL,
                    loser_earnings_before BIGINT NOT NULL,

                    winner_champion_before BOOLEAN NOT NULL,
                    loser_champion_before BOOLEAN NOT NULL,

                    winner_title_defenses_before INTEGER NOT NULL,
                    loser_title_defenses_before INTEGER NOT NULL,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    undone BOOLEAN NOT NULL DEFAULT FALSE
                );
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS undo_audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    fight_history_id BIGINT NOT NULL,
                    commissioner_id BIGINT NOT NULL,
                    commissioner_name TEXT NOT NULL,
                    fight_type TEXT NOT NULL,
                    winner_key TEXT NOT NULL,
                    loser_key TEXT NOT NULL,
                    score TEXT NOT NULL,
                    reversed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS result_override_log (
                    id BIGSERIAL PRIMARY KEY,
                    duplicate_history_id BIGINT NOT NULL,
                    commissioner_id BIGINT NOT NULL,
                    commissioner_name TEXT NOT NULL,
                    fight_type TEXT NOT NULL,
                    winner_key TEXT NOT NULL,
                    loser_key TEXT NOT NULL,
                    score TEXT NOT NULL,
                    overridden_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fight_night_sessions (
                    id BIGSERIAL PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'active',
                    started_by_id BIGINT NOT NULL,
                    started_by_name TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    start_history_id BIGINT NOT NULL DEFAULT 0,
                    ended_by_id BIGINT,
                    ended_by_name TEXT,
                    ended_at TIMESTAMPTZ,
                    end_history_id BIGINT
                );
            """)

            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_fight_night_session
                ON fight_night_sessions ((status))
                WHERE status = 'active';
            """)

            await conn.execute("""
                ALTER TABLE fight_history
                ADD COLUMN IF NOT EXISTS fight_night_session_id BIGINT;
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS fight_history_session_idx
                ON fight_history (fight_night_session_id);
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

def get_regular_purse(progression_rank, won=False):
    purses = {
        "Prospect": (50000, 25000),
        "Rising Prospect": (75000, 35000),
        "Contender": (100000, 50000),
        "Top Contender": (150000, 75000),
        "Elite Contender": (225000, 100000),
        "#1 Contender": (350000, 150000),
    }

    show_purse, win_bonus = purses.get(
        progression_rank,
        (50000, 25000)
    )

    if won:
        return show_purse + win_bonus

    return show_purse

async def update_division_rankings(division):
    rows = await bot.db.fetch(
        """
        SELECT fighter_key, fighter_name, wins, losses, rp, champion
        FROM fighters
        WHERE division = $1
        ORDER BY
            champion DESC,
            rp DESC,
            wins DESC,
            losses ASC,
            fighter_name ASC
        """,
        division
    )

    contender_rank = 1

    async with bot.db.acquire() as conn:
        async with conn.transaction():

            for row in rows:

                if row["champion"]:
                    await conn.execute(
                        """
                        UPDATE fighters
                        SET division_rank = NULL,
                            updated_at = NOW()
                        WHERE fighter_key = $1
                        """,
                        row["fighter_key"]
                    )

                else:
                    await conn.execute(
                        """
                        UPDATE fighters
                        SET division_rank = $1,
                            updated_at = NOW()
                        WHERE fighter_key = $2
                        """,
                        contender_rank,
                        row["fighter_key"]
                    )

                    contender_rank += 1
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
# SYSTEM HEALTH CHECK
# Commissioner-only, read-only diagnostic
# =========================================================

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def systemcheck(ctx):
    checks = []
    warnings = []

    # Database connectivity + required tables
    required_tables = [
        "fighters",
        "fight_history",
        "undo_audit_log",
        "result_override_log",
        "fight_night_sessions",
    ]

    try:
        async with bot.db.acquire() as conn:
            await conn.fetchval("SELECT 1")
            checks.append("✅ Database connection")

            table_rows = await conn.fetch(
                """
                SELECT tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname = 'public'
                  AND tablename = ANY($1::text[])
                """,
                required_tables,
            )
            existing_tables = {row["tablename"] for row in table_rows}

            for table in required_tables:
                if table in existing_tables:
                    checks.append(f"✅ Table: `{table}`")
                else:
                    warnings.append(f"❌ Missing table: `{table}`")

            fighter_count = await conn.fetchval("SELECT COUNT(*) FROM fighters")
            active_fights = await conn.fetchval(
                "SELECT COUNT(*) FROM fight_history WHERE undone = FALSE"
            )
            reversed_fights = await conn.fetchval(
                "SELECT COUNT(*) FROM fight_history WHERE undone = TRUE"
            )
            override_count = await conn.fetchval(
                "SELECT COUNT(*) FROM result_override_log"
            )

            # Champion integrity: zero or one champion per division is valid.
            champion_rows = await conn.fetch(
                """
                SELECT division, COUNT(*) AS champion_count
                FROM fighters
                WHERE champion = TRUE
                GROUP BY division
                ORDER BY division
                """
            )
            champion_counts = {
                row["division"]: row["champion_count"]
                for row in champion_rows
            }

            for division in ("Lightweight", "Middleweight", "Heavyweight"):
                count = champion_counts.get(division, 0)
                if count <= 1:
                    checks.append(
                        f"✅ {division} champion integrity: **{count}** active"
                    )
                else:
                    warnings.append(
                        f"❌ {division} has **{count}** active champions"
                    )

    except Exception as exc:
        warnings.append(
            f"❌ Database diagnostic failed: `{type(exc).__name__}`"
        )
        fighter_count = "?"
        active_fights = "?"
        reversed_fights = "?"
        override_count = "?"

    # Verify the core commands are registered in Discord.py.
    core_commands = [
        "result",
        "champresult",
        "forcechampresult",
        "undoresult",
        "confirmundo",
        "fighthistory",
        "startfightnight",
        "fightnightstatus",
        "fightnightrecap",
        "fightnightlist",
        "endfightnight",
    ]

    missing_commands = []
    for command_name in core_commands:
        if bot.get_command(command_name) is None:
            missing_commands.append(command_name)

    if missing_commands:
        warnings.append(
            "❌ Missing core commands: "
            + ", ".join(f"`!{name}`" for name in missing_commands)
        )
    else:
        checks.append("✅ Core result/undo/override commands registered")

    healthy = not warnings
    embed = discord.Embed(
        title="🩺 OSBL SYSTEM CHECK",
        description=(
            "**ALL CORE SYSTEMS HEALTHY**"
            if healthy
            else "**ATTENTION REQUIRED — review warnings below**"
        ),
        color=discord.Color.green() if healthy else discord.Color.orange(),
    )

    embed.add_field(
        name="🔎 Core Checks",
        value="\n".join(checks) if checks else "No successful checks recorded.",
        inline=False,
    )

    embed.add_field(
        name="📊 Live Database Snapshot",
        value=(
            f"Registered Fighters: **{fighter_count}**\n"
            f"Active Fight Records: **{active_fights}**\n"
            f"Reversed Fight Records: **{reversed_fights}**\n"
            f"Commissioner Overrides Logged: **{override_count}**"
        ),
        inline=False,
    )

    if warnings:
        embed.add_field(
            name="⚠️ Warnings",
            value="\n".join(warnings),
            inline=False,
        )

    embed.set_footer(text=f"Requested by {ctx.author.display_name} • Read-only diagnostic")
    await ctx.send(embed=embed)


# =========================================================
# FIGHT NIGHT SESSION SYSTEM
# Creates a clean event boundary without blocking normal results.
# =========================================================

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def startfightnight(ctx):
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            active = await conn.fetchrow(
                """
                SELECT id, started_by_name, started_at
                FROM fight_night_sessions
                WHERE status = 'active'
                ORDER BY id DESC
                LIMIT 1
                FOR UPDATE
                """
            )

            if active:
                await ctx.send(
                    "⚠️ **FIGHT NIGHT ALREADY ACTIVE**\n"
                    f"Session ID: **{active['id']}**\n"
                    f"Started by: **{active['started_by_name']}**\n"
                    "Use `!fightnightstatus` for the live session."
                )
                return

            start_history_id = await conn.fetchval(
                "SELECT COALESCE(MAX(id), 0) FROM fight_history"
            )

            session = await conn.fetchrow(
                """
                INSERT INTO fight_night_sessions (
                    started_by_id,
                    started_by_name,
                    start_history_id
                )
                VALUES ($1, $2, $3)
                RETURNING id, started_at
                """,
                ctx.author.id,
                ctx.author.display_name,
                start_history_id,
            )

    embed = discord.Embed(
        title="🥊 OSBL FIGHT NIGHT — SESSION OPEN",
        description="**OFFICIAL FIGHT NIGHT IS NOW ACTIVE**",
        color=discord.Color.green(),
    )
    embed.add_field(name="Session ID", value=str(session["id"]), inline=True)
    embed.add_field(name="Opened By", value=ctx.author.display_name, inline=True)
    embed.add_field(
        name="Starting Ledger Point",
        value=f"History ID **{start_history_id}**",
        inline=False,
    )
    embed.add_field(
        name="Commissioner Commands",
        value="`!fightnightstatus` • `!endfightnight`",
        inline=False,
    )
    embed.set_footer(text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION.")
    await ctx.send(embed=embed)


@bot.command()
async def fightnightstatus(ctx):
    async with bot.db.acquire() as conn:
        session = await conn.fetchrow(
            """
            SELECT *
            FROM fight_night_sessions
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """
        )

        if not session:
            await ctx.send(
                "⚫ **NO ACTIVE FIGHT NIGHT SESSION**\n"
                "A commissioner can open one with `!startfightnight`."
            )
            return

        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE undone = FALSE) AS official_fights,
                COUNT(*) FILTER (WHERE undone = TRUE) AS reversed_fights,
                COUNT(*) FILTER (
                    WHERE undone = FALSE AND fight_type = 'regular'
                ) AS regular_fights,
                COUNT(*) FILTER (
                    WHERE undone = FALSE AND fight_type = 'championship'
                ) AS championship_fights
            FROM fight_history
            WHERE id > $1
            """,
            session["start_history_id"],
        )

        override_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM result_override_log
            WHERE overridden_at >= $1
            """,
            session["started_at"],
        )

    embed = discord.Embed(
        title="📡 OSBL FIGHT NIGHT STATUS",
        description="🟢 **SESSION ACTIVE**",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Session ID", value=str(session["id"]), inline=True)
    embed.add_field(name="Opened By", value=session["started_by_name"], inline=True)
    embed.add_field(
        name="🥊 Live Fight Count",
        value=(
            f"Official: **{stats['official_fights']}**\n"
            f"Regular: **{stats['regular_fights']}**\n"
            f"Championship: **{stats['championship_fights']}**\n"
            f"Reversed: **{stats['reversed_fights']}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ Commissioner Overrides",
        value=f"**{override_count}** logged during this session",
        inline=False,
    )
    embed.set_footer(text="Live read-only Fight Night session status")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def endfightnight(ctx):
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            session = await conn.fetchrow(
                """
                SELECT *
                FROM fight_night_sessions
                WHERE status = 'active'
                ORDER BY id DESC
                LIMIT 1
                FOR UPDATE
                """
            )

            if not session:
                await ctx.send(
                    "⚠️ **NO ACTIVE FIGHT NIGHT SESSION**\n"
                    "There is nothing to close."
                )
                return

            end_history_id = await conn.fetchval(
                "SELECT COALESCE(MAX(id), 0) FROM fight_history"
            )

            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE undone = FALSE) AS official_fights,
                    COUNT(*) FILTER (WHERE undone = TRUE) AS reversed_fights,
                    COUNT(*) FILTER (
                        WHERE undone = FALSE AND fight_type = 'regular'
                    ) AS regular_fights,
                    COUNT(*) FILTER (
                        WHERE undone = FALSE AND fight_type = 'championship'
                    ) AS championship_fights
                FROM fight_history
                WHERE id > $1
                  AND id <= $2
                """,
                session["start_history_id"],
                end_history_id,
            )

            override_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM result_override_log
                WHERE overridden_at >= $1
                  AND overridden_at <= NOW()
                """,
                session["started_at"],
            )

            closed = await conn.fetchrow(
                """
                UPDATE fight_night_sessions
                SET status = 'closed',
                    ended_by_id = $1,
                    ended_by_name = $2,
                    ended_at = NOW(),
                    end_history_id = $3
                WHERE id = $4
                RETURNING ended_at
                """,
                ctx.author.id,
                ctx.author.display_name,
                end_history_id,
                session["id"],
            )

    embed = discord.Embed(
        title="🔒 OSBL FIGHT NIGHT — SESSION CLOSED",
        description="**OFFICIAL FIGHT NIGHT HAS ENDED**",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Session ID", value=str(session["id"]), inline=True)
    embed.add_field(name="Opened By", value=session["started_by_name"], inline=True)
    embed.add_field(name="Closed By", value=ctx.author.display_name, inline=True)
    embed.add_field(
        name="📊 Final Fight Night Recap",
        value=(
            f"Official Fights: **{stats['official_fights']}**\n"
            f"Regular Fights: **{stats['regular_fights']}**\n"
            f"Championship Fights: **{stats['championship_fights']}**\n"
            f"Reversed Results: **{stats['reversed_fights']}**\n"
            f"Commissioner Overrides: **{override_count}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Ledger Range",
        value=(
            f"Started after History ID **{session['start_history_id']}**\n"
            f"Closed at History ID **{end_history_id}**"
        ),
        inline=False,
    )
    embed.set_footer(text="Fight Night session archived in the OSBL database")
    await ctx.send(embed=embed)


@bot.command()
async def fightnightlist(ctx):
    """Show the most recent Fight Night sessions and their archived fight counts."""
    async with bot.db.acquire() as conn:
        sessions = await conn.fetch(
            """
            SELECT *
            FROM fight_night_sessions
            ORDER BY id DESC
            LIMIT 10
            """
        )

        if not sessions:
            await ctx.send(
                "📚 **NO FIGHT NIGHT SESSIONS FOUND**\n"
                "A commissioner can open the first one with `!startfightnight`."
            )
            return

        session_summaries = []
        for session in sessions:
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE undone = FALSE) AS official_fights,
                    COUNT(*) FILTER (
                        WHERE undone = FALSE AND fight_type = 'regular'
                    ) AS regular_fights,
                    COUNT(*) FILTER (
                        WHERE undone = FALSE AND fight_type = 'championship'
                    ) AS championship_fights,
                    COUNT(*) FILTER (WHERE undone = TRUE) AS reversed_fights
                FROM fight_history fh
                WHERE
                    fh.fight_night_session_id = $1
                    OR (
                        fh.fight_night_session_id IS NULL
                        AND fh.id > $2
                        AND ($3::BIGINT IS NULL OR fh.id <= $3)
                    )
                """,
                session["id"],
                session["start_history_id"],
                session["end_history_id"],
            )

            override_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM result_override_log
                WHERE overridden_at >= $1
                  AND ($2::TIMESTAMPTZ IS NULL OR overridden_at <= $2)
                """,
                session["started_at"],
                session["ended_at"],
            )

            status_icon = "🟢" if session["status"] == "active" else "🔒"
            status_text = "ACTIVE" if session["status"] == "active" else "CLOSED"
            started_timestamp = int(session["started_at"].timestamp())

            staff_line = f"Opened by **{session['started_by_name']}**"
            if session["ended_by_name"]:
                staff_line += f" • Closed by **{session['ended_by_name']}**"

            session_summaries.append(
                f"**Session {session['id']}** • {status_icon} **{status_text}**\n"
                f"{staff_line}\n"
                f"🕒 <t:{started_timestamp}:f>\n"
                f"🥊 Official **{stats['official_fights']}** • "
                f"Regular **{stats['regular_fights']}** • "
                f"Championship **{stats['championship_fights']}** • "
                f"Reversed **{stats['reversed_fights']}** • "
                f"Overrides **{override_count}**\n"
                f"📜 `!fightnightrecap {session['id']}`"
            )

    embed = discord.Embed(
        title="📚 OSBL FIGHT NIGHT ARCHIVE",
        description="**Most Recent Fight Night Sessions**",
        color=discord.Color.gold(),
    )

    # Stay safely under Discord's 1024-character field limit.
    chunks = []
    current = ""
    for summary in session_summaries:
        addition = summary if not current else "\n\n" + summary
        if len(current) + len(addition) > 1000:
            chunks.append(current)
            current = summary
        else:
            current += addition
    if current:
        chunks.append(current)

    for index, chunk in enumerate(chunks, start=1):
        field_name = "🥊 Sessions" if index == 1 else f"🥊 Sessions Continued ({index})"
        embed.add_field(name=field_name, value=chunk, inline=False)

    embed.set_footer(text="Use !fightnightrecap <Session ID> for the full archived card")
    await ctx.send(embed=embed)


@bot.command()
async def fightnightrecap(ctx, session_id: int = None):
    async with bot.db.acquire() as conn:
        if session_id is None:
            session = await conn.fetchrow(
                """
                SELECT *
                FROM fight_night_sessions
                ORDER BY id DESC
                LIMIT 1
                """
            )
        else:
            session = await conn.fetchrow(
                """
                SELECT *
                FROM fight_night_sessions
                WHERE id = $1
                """,
                session_id,
            )

        if not session:
            await ctx.send(
                "❌ **FIGHT NIGHT SESSION NOT FOUND**\n"
                "Use `!fightnightrecap <Session ID>`."
            )
            return

        fights = await conn.fetch(
            """
            SELECT
                fh.id,
                fh.fight_type,
                fh.winner_key,
                fh.loser_key,
                fh.score,
                fh.undone,
                fw.fighter_name AS winner_name,
                fl.fighter_name AS loser_name
            FROM fight_history fh
            LEFT JOIN fighters fw ON fw.fighter_key = fh.winner_key
            LEFT JOIN fighters fl ON fl.fighter_key = fh.loser_key
            WHERE
                fh.fight_night_session_id = $1
                OR (
                    fh.fight_night_session_id IS NULL
                    AND fh.id > $2
                    AND ($3::BIGINT IS NULL OR fh.id <= $3)
                )
            ORDER BY fh.id ASC
            """,
            session["id"],
            session["start_history_id"],
            session["end_history_id"],
        )

    status_text = "🟢 ACTIVE" if session["status"] == "active" else "🔒 CLOSED"
    embed = discord.Embed(
        title=f"📜 OSBL FIGHT NIGHT RECAP — SESSION {session['id']}",
        description=f"{status_text}\n**Official Fight Ledger**",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Session Staff",
        value=(
            f"Opened by: **{session['started_by_name']}**\n"
            + (
                f"Closed by: **{session['ended_by_name']}**"
                if session["ended_by_name"]
                else "Closed by: **—**"
            )
        ),
        inline=False,
    )

    if not fights:
        embed.add_field(
            name="🥊 Recorded Fights",
            value="No fights were recorded during this session.",
            inline=False,
        )
    else:
        lines = []
        for fight in fights:
            winner_name = fight["winner_name"] or fight["winner_key"]
            loser_name = fight["loser_name"] or fight["loser_key"]
            fight_label = (
                "Championship"
                if fight["fight_type"] == "championship"
                else "Regular"
            )
            result_status = "↩️ REVERSED" if fight["undone"] else "✅ OFFICIAL"
            lines.append(
                f"**#{fight['id']}** • {fight_label} • {result_status}\n"
                f"🏆 **{winner_name}** def. **{loser_name}** • {fight['score']}"
            )

        # Discord embed field values max out at 1024 characters.
        chunks = []
        current = ""
        for line in lines:
            addition = line if not current else "\n\n" + line
            if len(current) + len(addition) > 1000:
                chunks.append(current)
                current = line
            else:
                current += addition
        if current:
            chunks.append(current)

        for index, chunk in enumerate(chunks, start=1):
            field_name = "🥊 Fight Card Results" if index == 1 else f"🥊 Results Continued ({index})"
            embed.add_field(name=field_name, value=chunk, inline=False)

    embed.add_field(
        name="Ledger Range",
        value=(
            f"Started after History ID **{session['start_history_id']}**\n"
            + (
                f"Closed at History ID **{session['end_history_id']}**"
                if session["end_history_id"] is not None
                else "Session is still active"
            )
        ),
        inline=False,
    )
    embed.set_footer(text="Use !fightnightrecap <Session ID> to review archived Fight Nights")
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

    await update_division_rankings(row["division"])

    row = await bot.db.fetchrow(
    """
    SELECT *
    FROM fighters
    WHERE fighter_key = $1
    """,
    fighter_key
)

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

    winner_purse = get_regular_purse(winner["progression_rank"], won=True)
    loser_purse = get_regular_purse(loser["progression_rank"], won=False)

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

       duplicate = await conn.fetchrow(
        """
        SELECT id, created_at
        FROM fight_history
        WHERE fight_type = $1
          AND winner_key = $2
          AND loser_key = $3
          AND score = $4
          AND undone = FALSE
          AND created_at >= NOW() - INTERVAL '10 minutes'
        ORDER BY id DESC
        LIMIT 1
        """,
        "regular",
        winner_key,
        loser_key,
        score
       )

       if duplicate:
           await ctx.send(
            f"⚠️ **POSSIBLE DUPLICATE RESULT**\n"
            f"This fight appears to have already been recorded.\n"
            f"History ID: **{duplicate['id']}**\n"
            f"No records, RP, rankings, or payouts were changed."
           )
           return

       fight_night_session_id = await conn.fetchval(
           """
           SELECT id
           FROM fight_night_sessions
           WHERE status = 'active'
           ORDER BY id DESC
           LIMIT 1
           """
       )

       async with conn.transaction():

           await conn.execute(
            """
            INSERT INTO fight_history (
                fight_type,
                winner_key,
                loser_key,
                score,
                winner_rp_before,
                loser_rp_before,
                winner_wins_before,
                winner_losses_before,
                loser_wins_before,
                loser_losses_before,
                winner_earnings_before,
                loser_earnings_before,
                winner_champion_before,
                loser_champion_before,
                winner_title_defenses_before,
                loser_title_defenses_before,
                fight_night_session_id
            )
            VALUES (
                'regular',
                $1, $2, $3,
                $4, $5,
                $6, $7,
                $8, $9,
                $10, $11,
                $12, $13,
                $14, $15,
                $16
            )
            """,
            winner_key,
            loser_key,
            score,
            winner["rp"],
            loser["rp"],
            winner["wins"],
            winner["losses"],
            loser["wins"],
            loser["losses"],
            winner["career_earnings"],
            loser["career_earnings"],
            winner["champion"],
            loser["champion"],
            winner["title_defenses"],
            loser["title_defenses"],
            fight_night_session_id
        )

           await conn.execute(
            """
            UPDATE fighters
    SET wins = wins + 1,
        rp = $1,
        progression_rank = $2,
        career_earnings = career_earnings + $3,
        updated_at = NOW()
    WHERE fighter_key = $4
    """,
    new_winner_rp,
    winner_progression,
    winner_purse,
    winner_key
)

           await conn.execute(
            """
          UPDATE fighters
     SET losses = losses + 1,
         rp = $1,
         progression_rank = $2,
         career_earnings = career_earnings + $3,
         updated_at = NOW()
     WHERE fighter_key = $4
     """,
     new_loser_rp,
     loser_progression,
     loser_purse,
     loser_key
 )

    await update_division_rankings(winner["division"])
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
            f"💰 Fight Purse: **${winner_purse:,}**\n"
            f"💵 Career Earnings: **${winner['career_earnings'] + winner_purse:,}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🥊 Opponent",
        value=(
            f"**{loser['fighter_name']}**\n"
            f"Record: **{loser['wins']}-{loser['losses'] + 1}**\n"
            f"RP: **{new_loser_rp}** (+{loser_rp_gain})\n"
            f"Progression: **{loser_progression}**\n"
            f"💰 Fight Purse: **${loser_purse:,}**\n"
            f"💵 Career Earnings: **${loser['career_earnings'] + loser_purse:,}**"
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

    force_override = bool(getattr(ctx, "_osbl_force_champresult", False))

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

    winner_purse = 5000000
    loser_purse = 500000

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

        duplicate = await conn.fetchrow(
    """
    SELECT id, created_at
    FROM fight_history
    WHERE fight_type = $1
      AND winner_key = $2
      AND loser_key = $3
      AND score = $4
      AND undone = FALSE
      AND created_at >= NOW() - INTERVAL '10 minutes'
    ORDER BY id DESC
    LIMIT 1
    """,
    "championship",
    winner_key,
    loser_key,
    score
)

        if duplicate and not force_override:
            await ctx.send(
                f"⚠️ **POSSIBLE DUPLICATE CHAMPIONSHIP RESULT**\n"
                f"This championship fight appears to have already been recorded.\n"
                f"History ID: **{duplicate['id']}**\n"
                f"No records, RP, rankings, titles, or payouts were changed.\n\n"
                f"Commissioner override: `!forcechampresult Winner | Loser | Score`"
            )
            return

        fight_night_session_id = await conn.fetchval(
            """
            SELECT id
            FROM fight_night_sessions
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """
        )

        async with conn.transaction():
            await conn.execute(
                    """
                    INSERT INTO fight_history (
                        fight_type,
                        winner_key,
                        loser_key,
                        score,
                        winner_rp_before,
                        loser_rp_before,
                        winner_wins_before,
                        winner_losses_before,
                        loser_wins_before,
                        loser_losses_before,
                        winner_earnings_before,
                        loser_earnings_before,
                        winner_champion_before,
                        loser_champion_before,
                        winner_title_defenses_before,
                        loser_title_defenses_before,
                        fight_night_session_id
                    )
                    VALUES (
                        'championship',
                        $1, $2, $3,
                        $4, $5,
                        $6, $7,
                        $8, $9,
                        $10, $11,
                        $12, $13,
                        $14, $15,
                        $16
                    )
                    """,
                    winner_key,
                    loser_key,
                    score,
                    winner["rp"],
                    loser["rp"],
                    winner["wins"],
                    winner["losses"],
                    loser["wins"],
                    loser["losses"],
                    winner["career_earnings"],
                    loser["career_earnings"],
                    winner["champion"],
                    loser["champion"],
                    winner["title_defenses"],
                    loser["title_defenses"],
                    fight_night_session_id
                )

            await conn.execute(
                """
                UPDATE fighters
                SET wins = wins + 1,
                    rp = $1,
                    progression_rank = $2,
                    champion = TRUE,
                    title_defenses = title_defenses + $3,
                    career_earnings = career_earnings + $4,
                    updated_at = NOW()
                WHERE fighter_key = $5
                """,
                new_winner_rp,
                winner_progression,
                1 if defending_champion else 0,
                winner_purse,
                winner_key
            )

            await conn.execute(
                """
                UPDATE fighters
                SET losses = losses + 1,
                    rp = $1,
                    progression_rank = $2,
                    champion = FALSE,
                    career_earnings = career_earnings + $3,
                    updated_at = NOW()
                WHERE fighter_key = $4
                """,
                new_loser_rp,
                loser_progression,
                loser_purse,
                loser_key
            )

            if duplicate and force_override:
                await conn.execute(
                    """
                    INSERT INTO result_override_log (
                        duplicate_history_id,
                        commissioner_id,
                        commissioner_name,
                        fight_type,
                        winner_key,
                        loser_key,
                        score
                    )
                    VALUES ($1, $2, $3, 'championship', $4, $5, $6)
                    """,
                    duplicate["id"],
                    ctx.author.id,
                    str(ctx.author),
                    winner_key,
                    loser_key,
                    score
                )

    await update_division_rankings(winner["division"])
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
            f"Progression: **{winner_progression}**\n"
            f"💰 Fight Purse: **${winner_purse:,}**\n"
            f"💵 Career Earnings: **${winner['career_earnings'] + winner_purse:,}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🥊 Opponent",
        value=(
            f"**{loser['fighter_name']}**\n"
            f"Record: **{loser['wins']}-{loser['losses'] + 1}**\n"
            f"RP: **{new_loser_rp}** (+{loser_rp_gain})\n"
            f"Progression: **{loser_progression}**\n"
            f"💰 Fight Purse: **${loser_purse:,}**\n"
            f"💵 Career Earnings: **${loser['career_earnings'] + loser_purse:,}**"
            
        ),
        inline=False
    )

    embed.add_field(
        name="⭐ Championship Bonuses",
        value=bonuses_text,
        inline=False
    )

    if duplicate and force_override:
        embed.add_field(
            name="🛡️ Commissioner Duplicate Override",
            value=(
                f"Authorized by **{ctx.author.display_name}**\n"
                f"Previous matching History ID: **{duplicate['id']}**"
            ),
            inline=False
        )

    embed.set_footer(
        text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    await ctx.send(embed=embed)

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def forcechampresult(ctx, *, details: str = None):
    """Commissioner-only override for a legitimate duplicate championship result."""
    ctx._osbl_force_champresult = True
    try:
        await champresult.callback(ctx, details=details)
    finally:
        ctx._osbl_force_champresult = False


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

# ============================================
# OSBL DIVISION RANKINGS
# FORMAT:
# !rankings Lightweight
# !rankings Middleweight
# !rankings Heavyweight
# ============================================

@bot.command()
async def rankings(ctx, *, division: str = None):

    if not division:
        await ctx.send(
            "❌ Use:\n"
            "`!rankings Lightweight`\n"
            "`!rankings Middleweight`\n"
            "`!rankings Heavyweight`"
        )
        return

    divisions = {
        "lightweight": "Lightweight",
        "middleweight": "Middleweight",
        "heavyweight": "Heavyweight"
    }

    division_key = division.lower().strip()

    if division_key not in divisions:
        await ctx.send(
            "❌ Division must be **Lightweight, Middleweight, or Heavyweight**."
        )
        return

    official_division = divisions[division_key]

    await update_division_rankings(official_division)

    champion = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE division = $1
          AND champion = TRUE
        LIMIT 1
        """,
        official_division
    )

    fighters = await bot.db.fetch(
        """
        SELECT *
        FROM fighters
        WHERE division = $1
          AND champion = FALSE
        ORDER BY division_rank ASC
        """,
        official_division
    )

    embed = discord.Embed(
        title=f"🥊 OSBL {official_division.upper()} RANKINGS",
        description="Official ONESTATE Boxing League division standings",
        color=discord.Color.gold()
    )

    if champion:
        embed.add_field(
            name="👑 CHAMPION",
            value=(
                f"**{champion['fighter_name']}**\n"
                f"Record: **{champion['wins']}-{champion['losses']}**\n"
                f"RP: **{champion['rp']}**\n"
                f"Title Defenses: **{champion['title_defenses']}**"
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="👑 CHAMPION",
            value="**VACANT**",
            inline=False
        )

    ranking_lines = [
    (
        f"**#{fighter['division_rank']} — {fighter['fighter_name']}**\n"
        f"🏢 Gym: **{fighter['gym']}**\n"
        f"🥊 Record: **{fighter['wins']}-{fighter['losses']}**\n"
        f"💎 RP: **{fighter['rp']}**\n"
        f"📈 Progression: **{fighter['progression_rank']}**\n"
        f"{'🏆 TITLE ELIGIBLE' if fighter['rp'] >= 140 else '🔒 ' + str(140 - fighter['rp']) + ' RP away from Title Eligibility'}"
    )
    for fighter in fighters
]

    ranking_chunks = []
    current_chunk = ""

    for line in ranking_lines:
        entry = line + "\n\n"

        if len(current_chunk) + len(entry) > 1000:
            ranking_chunks.append(current_chunk)
            current_chunk = entry
        else:
            current_chunk += entry

    if current_chunk:
        ranking_chunks.append(current_chunk)

    if ranking_chunks:
        for i, chunk in enumerate(ranking_chunks):
            embed.add_field(
                name="🥇 CONTENDER RANKINGS" if i == 0 else "🥊 CONTENDER RANKINGS CONT.",
                value=chunk,
                inline=False
            )
    else:
        embed.add_field(
            name="🥇 CONTENDER RANKINGS",
            value="No ranked contenders yet.",
            inline=False
        )

    embed.set_footer(
        text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    await ctx.send(embed=embed)

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def undoresult(ctx):
    async with bot.db.acquire() as conn:
        fight = await conn.fetchrow(
            """
            SELECT *
            FROM fight_history
            WHERE undone = FALSE
            ORDER BY id DESC
            LIMIT 1
            """
        )

        if not fight:
            await ctx.send(
                "❌ **There are no fight results available to undo.**"
            )
            return

        winner = await conn.fetchrow(
            """
            SELECT fighter_name
            FROM fighters
            WHERE fighter_key = $1
            """,
            fight["winner_key"]
        )

        loser = await conn.fetchrow(
            """
            SELECT fighter_name
            FROM fighters
            WHERE fighter_key = $1
            """,
            fight["loser_key"]
        )

    fight_type = (
        "Championship Fight"
        if fight["fight_type"] == "championship"
        else "Regular Fight"
    )

    embed = discord.Embed(
        title="⚠️ OSBL UNDO CONFIRMATION REQUIRED",
        description=(
            "**No changes have been made yet.**\n"
            "Review this result before confirming."
        ),
        color=discord.Color.gold()
    )

    embed.add_field(
        name="🥊 Result Selected",
        value=(
            f"Type: **{fight_type}**\n"
            f"Winner: **{winner['fighter_name']}**\n"
            f"Opponent: **{loser['fighter_name']}**\n"
            f"Score: **{fight['score']}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🔐 Confirmation",
        value=(
            f"Fight History ID: **{fight['id']}**\n\n"
            f"To reverse this result, enter:\n"
            f"`!confirmundo {fight['id']}`"
        ),
        inline=False
    )

    embed.set_footer(
        text="OSBL COMMISSIONER • VERIFY BEFORE CONFIRMING"
    )

    await ctx.send(embed=embed)

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def confirmundo(ctx, fight_id: int):
    async with bot.db.acquire() as conn:
        async with conn.transaction():

            fight = await conn.fetchrow(
                """
                SELECT *
                FROM fight_history
                WHERE id = $1
                  AND undone = FALSE
                FOR UPDATE
                """,
                fight_id
            )

            if not fight:
                await ctx.send(
                    "❌ **That fight cannot be undone. "
                    "It may already have been reversed or the ID is invalid.**"
                )
                return

            newest_fight = await conn.fetchval(
                """
                SELECT id
                FROM fight_history
                WHERE undone = FALSE
                ORDER BY id DESC
                LIMIT 1
                """
            )

            if newest_fight != fight_id:
                await ctx.send(
                    "❌ **Safety lock: only the most recent active "
                    "fight result can be undone.**"
                )
                return

            winner = await conn.fetchrow(
                """
                SELECT fighter_key, fighter_name, division
                FROM fighters
                WHERE fighter_key = $1
                """,
                fight["winner_key"]
            )

            loser = await conn.fetchrow(
                """
                SELECT fighter_key, fighter_name, division
                FROM fighters
                WHERE fighter_key = $1
                """,
                fight["loser_key"]
            )

            if not winner or not loser:
                await ctx.send(
                    "❌ **Undo failed: one of the fighters could not be found.**"
                )
                return

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

            winner_progression = progression_for(
                fight["winner_rp_before"]
            )

            loser_progression = progression_for(
                fight["loser_rp_before"]
            )

            await conn.execute(
                """
                UPDATE fighters
                SET wins = $1,
                    losses = $2,
                    rp = $3,
                    progression_rank = $4,
                    career_earnings = $5,
                    champion = $6,
                    title_defenses = $7,
                    updated_at = NOW()
                WHERE fighter_key = $8
                """,
                fight["winner_wins_before"],
                fight["winner_losses_before"],
                fight["winner_rp_before"],
                winner_progression,
                fight["winner_earnings_before"],
                fight["winner_champion_before"],
                fight["winner_title_defenses_before"],
                fight["winner_key"]
            )

            await conn.execute(
                """
                UPDATE fighters
                SET wins = $1,
                    losses = $2,
                    rp = $3,
                    progression_rank = $4,
                    career_earnings = $5,
                    champion = $6,
                    title_defenses = $7,
                    updated_at = NOW()
                WHERE fighter_key = $8
                """,
                fight["loser_wins_before"],
                fight["loser_losses_before"],
                fight["loser_rp_before"],
                loser_progression,
                fight["loser_earnings_before"],
                fight["loser_champion_before"],
                fight["loser_title_defenses_before"],
                fight["loser_key"]
            )

            await conn.execute(
                """
                UPDATE fight_history
                SET undone = TRUE
                WHERE id = $1
                """,
                fight_id
            )

            await conn.execute(
                """
                INSERT INTO undo_audit_log (
                    fight_history_id,
                    commissioner_id,
                    commissioner_name,
                    fight_type,
                    winner_key,
                    loser_key,
                    score
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                fight_id,
                ctx.author.id,
                str(ctx.author),
                fight["fight_type"],
                fight["winner_key"],
                fight["loser_key"],
                fight["score"]
    )

    await update_division_rankings(winner["division"])

    if loser["division"] != winner["division"]:
        await update_division_rankings(loser["division"])

    fight_type = (
        "Championship Fight"
        if fight["fight_type"] == "championship"
        else "Regular Fight"
    )

    embed = discord.Embed(
        title="↩️ OSBL RESULT UNDONE",
        description=f"**{fight_type} has been officially reversed.**",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="🥊 Reversed Result",
        value=(
            f"**{winner['fighter_name']} vs {loser['fighter_name']}**\n"
            f"Score: **{fight['score']}**\n"
            f"History ID: **{fight_id}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🏆 Restored Fighter",
        value=(
            f"**{winner['fighter_name']}**\n"
            f"Record: **{fight['winner_wins_before']}-"
            f"{fight['winner_losses_before']}**\n"
            f"RP: **{fight['winner_rp_before']}**\n"
            f"Career Earnings: **${fight['winner_earnings_before']:,}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🥊 Restored Opponent",
        value=(
            f"**{loser['fighter_name']}**\n"
            f"Record: **{fight['loser_wins_before']}-"
            f"{fight['loser_losses_before']}**\n"
            f"RP: **{fight['loser_rp_before']}**\n"
            f"Career Earnings: **${fight['loser_earnings_before']:,}**"
        ),
        inline=False
    )

    embed.set_footer(
        text=f"OSBL COMMISSIONER • Reversed by {ctx.author.display_name}"
    )

    await ctx.send(embed=embed)

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def fighthistory(ctx, limit: int = 10):
    limit = max(1, min(limit, 20))

    async with bot.db.acquire() as conn:
        fights = await conn.fetch(
            """
            SELECT
                id,
                fight_type,
                winner_key,
                loser_key,
                score,
                created_at,
                undone
            FROM fight_history
            ORDER BY id DESC
            LIMIT $1
            """,
            limit
        )

    if not fights:
        await ctx.send("🥊 No official fight history has been recorded yet.")
        return

    embed = discord.Embed(
        title="📜 OSBL OFFICIAL FIGHT HISTORY",
        description=f"Showing the latest **{len(fights)}** recorded fights.",
        color=discord.Color.gold()
    )

    for fight in fights:
        status = "↩️ REVERSED" if fight["undone"] else "✅ OFFICIAL"

        fight_type = (
            "Championship Fight"
            if fight["fight_type"] == "championship"
            else "Regular Fight"
        )

        embed.add_field(
            name=f"🥊 History ID #{fight['id']} • {status}",
            value=(
                f"**Type:** {fight_type}\n"
                f"🏆 Winner: **{fight['winner_key']}**\n"
                f"🥊 Opponent: **{fight['loser_key']}**\n"
                f"📊 Score: **{fight['score']}**"
            ),
            inline=False
        )

    embed.set_footer(
        text="OSBL COMMISSIONER • OFFICIAL FIGHT LEDGER"
    )

    await ctx.send(embed=embed)

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def fighterhistory(ctx, *, fighter_name: str):
    async with bot.db.acquire() as conn:
        fighter = await conn.fetchrow(
            """
            SELECT fighter_key, fighter_name
            FROM fighters
            WHERE LOWER(fighter_name) = LOWER($1)
            """,
            fighter_name
        )

        if not fighter:
            await ctx.send(f"❌ Fighter not found: **{fighter_name}**")
            return

        fights = await conn.fetch(
            """
            SELECT
                id,
                fight_type,
                winner_key,
                loser_key,
                score,
                created_at,
                undone
            FROM fight_history
            WHERE winner_key = $1 OR loser_key = $1
            ORDER BY id DESC
            LIMIT 20
            """,
            fighter["fighter_key"]
        )

    if not fights:
        await ctx.send(
            f"🥊 No fight history found for **{fighter['fighter_name']}**."
        )
        return

    embed = discord.Embed(
        title="🥊 OSBL FIGHTER HISTORY",
        description=f"Official fight ledger for **{fighter['fighter_name']}**",
        color=discord.Color.gold()
    )

    for fight in fights:
        status = "↩️ REVERSED" if fight["undone"] else "✅ OFFICIAL"

        fight_type = (
            "Championship Fight"
            if fight["fight_type"] == "championship"
            else "Regular Fight"
        )

        if fight["winner_key"] == fighter["fighter_key"]:
            result = "🏆 WIN"
            opponent_key = fight["loser_key"]
        else:
            result = "🥊 LOSS"
            opponent_key = fight["winner_key"]

        embed.add_field(
            name=f"History ID #{fight['id']} • {result} • {status}",
            value=(
                f"**Type:** {fight_type}\n"
                f"Opponent: **{opponent_key}**\n"
                f"📊 Score: **{fight['score']}**"
            ),
            inline=False
        )

    embed.set_footer(
        text="OSBL COMMISSIONER • OFFICIAL FIGHTER LEDGER"
    )

    await ctx.send(embed=embed)

@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def undolog(ctx, limit: int = 10):
    limit = max(1, min(limit, 20))

    async with bot.db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                fight_history_id,
                commissioner_name,
                fight_type,
                winner_key,
                loser_key,
                score,
                reversed_at
            FROM undo_audit_log
            ORDER BY reversed_at DESC
            LIMIT $1
            """,
            limit
        )

    if not rows:
        await ctx.send("↩️ No result reversals have been recorded yet.")
        return

    embed = discord.Embed(
        title="↩️ OSBL OFFICIAL UNDO LOG",
        description=f"Showing the latest **{len(rows)}** result reversals.",
        color=discord.Color.gold()
    )

    for row in rows:
        fight_type = (
            "Championship Fight"
            if row["fight_type"] == "championship"
            else "Regular Fight"
        )

        reversed_time = row["reversed_at"].strftime("%Y-%m-%d %H:%M")

        embed.add_field(
            name=f"History ID #{row['fight_history_id']} • REVERSED",
            value=(
                f"**Type:** {fight_type}\n"
                f"🥊 {row['winner_key']} vs {row['loser_key']}\n"
                f"📊 Score: **{row['score']}**\n"
                f"👤 Commissioner: **{row['commissioner_name']}**\n"
                f"🕒 Reversed: **{reversed_time}**"
            ),
            inline=False
        )

    embed.set_footer(
        text="OSBL COMMISSIONER • OFFICIAL REVERSAL AUDIT LOG"
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

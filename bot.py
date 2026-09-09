
import os
import re
import io
import json
import gzip
import base64
import shutil
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
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
                CREATE TABLE IF NOT EXISTS fight_bookings (
                    id BIGSERIAL PRIMARY KEY,
                    fighter1_key TEXT NOT NULL,
                    fighter1_name TEXT NOT NULL,
                    fighter2_key TEXT NOT NULL,
                    fighter2_name TEXT NOT NULL,
                    division TEXT NOT NULL,
                    bout_type TEXT NOT NULL DEFAULT 'regular',
                    status TEXT NOT NULL DEFAULT 'booked',
                    fight_night_session_id BIGINT,
                    booked_by_id BIGINT NOT NULL,
                    booked_by_name TEXT NOT NULL,
                    booked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    locked_by_id BIGINT,
                    locked_by_name TEXT,
                    locked_at TIMESTAMPTZ,
                    cancelled_by_id BIGINT,
                    cancelled_by_name TEXT,
                    cancelled_at TIMESTAMPTZ
                );
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS fight_bookings_status_idx
                ON fight_bookings (status, id);
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS fight_bookings_session_idx
                ON fight_bookings (fight_night_session_id);
            """)

            await conn.execute("""
                ALTER TABLE fighters
                ADD COLUMN IF NOT EXISTS photo_data BYTEA;
            """)

            await conn.execute("""
                ALTER TABLE fighters
                ADD COLUMN IF NOT EXISTS photo_filename TEXT;
            """)

            await conn.execute("""
                ALTER TABLE fighters
                ADD COLUMN IF NOT EXISTS photo_content_type TEXT;
            """)

            await conn.execute("""
                ALTER TABLE fighters
                ADD COLUMN IF NOT EXISTS photo_updated_at TIMESTAMPTZ;
            """)

            await conn.execute("""
                ALTER TABLE fighters
                ADD COLUMN IF NOT EXISTS photo_locked_by_id BIGINT;
            """)

            await conn.execute("""
                ALTER TABLE fighters
                ADD COLUMN IF NOT EXISTS photo_locked_by_name TEXT;
            """)

            await conn.execute("""
                ALTER TABLE fight_history
                ADD COLUMN IF NOT EXISTS fight_night_session_id BIGINT;
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS fight_history_session_idx
                ON fight_history (fight_night_session_id);
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS gym_settings (
                    official_name TEXT PRIMARY KEY,
                    promoter_name TEXT NOT NULL,
                    updated_by_id BIGINT,
                    updated_by_name TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS gym_management_log (
                    id BIGSERIAL PRIMARY KEY,
                    action TEXT NOT NULL,
                    fighter_key TEXT,
                    fighter_name TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    staff_id BIGINT NOT NULL,
                    staff_name TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS gym_season_history (
                    id BIGSERIAL PRIMARY KEY,
                    season_name TEXT NOT NULL,
                    gym_name TEXT NOT NULL,
                    promoter_name TEXT NOT NULL,
                    gym_rank INTEGER NOT NULL,
                    gym_points INTEGER NOT NULL,
                    wins INTEGER NOT NULL,
                    losses INTEGER NOT NULL,
                    total_rp INTEGER NOT NULL,
                    roster_size INTEGER NOT NULL,
                    champions INTEGER NOT NULL,
                    title_defenses INTEGER NOT NULL,
                    earnings BIGINT NOT NULL,
                    archived_by_id BIGINT NOT NULL,
                    archived_by_name TEXT NOT NULL,
                    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (season_name, gym_name)
                );
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS gym_season_history_season_idx
                ON gym_season_history (season_name, gym_rank);
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS test_cleanup_audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    cleanup_type TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    details TEXT,
                    commissioner_id BIGINT NOT NULL,
                    commissioner_name TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS payout_ledger (
                    id BIGSERIAL PRIMARY KEY,
                    fight_history_id BIGINT NOT NULL,
                    fight_night_session_id BIGINT,
                    fighter_key TEXT NOT NULL,
                    fighter_name TEXT NOT NULL,
                    gym_name TEXT NOT NULL,
                    division TEXT NOT NULL,
                    fight_type TEXT NOT NULL,
                    payout_role TEXT NOT NULL,
                    progression_rank TEXT NOT NULL,
                    amount BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    reversed_by_id BIGINT,
                    reversed_by_name TEXT,
                    reversed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (fight_history_id, fighter_key)
                );
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS payout_ledger_fighter_idx
                ON payout_ledger (fighter_key, created_at DESC);
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS payout_ledger_session_idx
                ON payout_ledger (fight_night_session_id, created_at DESC);
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS payout_ledger_status_idx
                ON payout_ledger (status, created_at DESC);
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS payout_requests (
                    id BIGSERIAL PRIMARY KEY,
                    fighter_key TEXT NOT NULL,
                    fighter_name TEXT NOT NULL,
                    requested_amount BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_by_id BIGINT NOT NULL,
                    requested_by_name TEXT NOT NULL,
                    approved_by_id BIGINT,
                    approved_by_name TEXT,
                    approved_at TIMESTAMPTZ,
                    rejected_by_id BIGINT,
                    rejected_by_name TEXT,
                    rejected_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS payout_requests_fighter_idx
                ON payout_requests (fighter_key, created_at DESC);
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS payout_requests_status_idx
                ON payout_requests (status, created_at DESC);
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS payout_cashouts (
                    id BIGSERIAL PRIMARY KEY,
                    request_id BIGINT,
                    fighter_key TEXT NOT NULL,
                    fighter_name TEXT NOT NULL,
                    amount BIGINT NOT NULL,
                    paid_by_id BIGINT NOT NULL,
                    paid_by_name TEXT NOT NULL,
                    note TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS payout_cashouts_fighter_idx
                ON payout_cashouts (fighter_key, created_at DESC);
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fighter_discord_links (
                    fighter_key TEXT PRIMARY KEY,
                    fighter_name TEXT NOT NULL,
                    discord_user_id BIGINT NOT NULL UNIQUE,
                    linked_by_id BIGINT NOT NULL,
                    linked_by_name TEXT NOT NULL,
                    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS fighter_discord_links_user_idx
                ON fighter_discord_links (discord_user_id);
            """)

            default_gyms = [
                ("RADEEMERS", "Dub Radeem"),
                ("ROYAL HITTAZ", "Stormi North"),
                ("FINESSE TOWN FIGHTERS", "Cheeda Finessa"),
                ("GROVE STREET GOATS", "Mr. Souls"),
            ]
            await conn.executemany(
                """
                INSERT INTO gym_settings (official_name, promoter_name)
                VALUES ($1, $2)
                ON CONFLICT (official_name) DO NOTHING
                """,
                default_gyms,
            )

        normalization = await _normalize_existing_gym_data()
        print(
            "🧹 OSBL Gym Normalization Ready — "
            f"{normalization['fighters_updated']} fighter record(s), "
            f"{normalization['ledger_rows_updated']} payout ledger row(s) normalized"
        )
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


POSTER_RENDERER_VERSION = "V5-CHAMPIONSHIP-2026-09-07"
RANKINGS_SYSTEM_VERSION = "V1-AUTO-RANKINGS-2026-09-08"
FIGHTER_PROFILE_VERSION = "V3-OFFICIAL-FIGHTER-CARDS-2026-09-08"
GYM_SYSTEM_VERSION = "V1-GYM-STANDINGS-2026-09-08"
GYM_POSTER_VERSION = "V2-CLEAN-GYM-POSTERS-2026-09-08"
GYM_MANAGEMENT_VERSION = "V1-GYM-MANAGEMENT-2026-09-08"
GYM_HISTORY_VERSION = "V1-GYM-HISTORY-2026-09-08"
GYM_NORMALIZATION_VERSION = "V1-GYM-NORMALIZATION-2026-09-09"
CLEANUP_SYSTEM_VERSION = "V1-TEST-CLEANUP-2026-09-08"
DATABASE_BACKUP_VERSION = "V1-DATABASE-BACKUP-2026-09-08"
PAYOUT_SYSTEM_VERSION = "V5-TREASURY-DASHBOARD-2026-09-09"

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
        "fight_bookings",
        "gym_settings",
        "gym_management_log",
        "gym_season_history",
        "test_cleanup_audit_log",
        "payout_ledger",
        "payout_requests",
        "payout_cashouts",
        "fighter_discord_links",
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
            pending_bookings = await conn.fetchval(
                "SELECT COUNT(*) FROM fight_bookings WHERE status IN ('booked', 'locked')"
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
        pending_bookings = "?"

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
        "matchupcheck",
        "bookfight",
        "fightcard",
        "fightposter",
        "fightposterall",
        "lockfight",
        "cancelbookedfight",
        "setfighterphoto",
        "fighterphoto",
        "removefighterphoto",
        "rankings",
        "allrankings",
        "top10",
        "fighter",
        "fightercard",
        "setgym",
        "removegym",
        "setpromoter",
        "gymcheck",
        "archivegymseason",
        "gymseason",
        "gymhistory",
        "gymseasonlist",
        "testfighterlist",
        "deletetestfighter",
        "confirmdeletetestfighter",
        "deletetestseason",
        "confirmdeletetestseason",
        "databasebackup",
        "payoutdesk",
        "fightpayout",
        "fighterpayout",
        "payoutreport",
        "payoutaudit",
        "fighterbank",
        "payoutrequest",
        "payoutrequests",
        "payfighter",
        "rejectpayout",
        "payoutstatement",
        "linkfighterdiscord",
        "fighterdiscord",
        "unlinkfighterdiscord",
        "resendreceipt",
        "treasury",
        "gymtreasury",
        "normalizegyms",
        "treasurytop",
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
        checks.append("✅ Core result/undo/matchmaking/photo/poster commands registered")

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
            f"Commissioner Overrides Logged: **{override_count}**\n"
            f"Pending/Locked Matchups: **{pending_bookings}**"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎨 Poster Renderer",
        value=f"**{POSTER_RENDERER_VERSION}**",
        inline=False,
    )

    embed.add_field(
        name="📊 Rankings System",
        value=f"**{RANKINGS_SYSTEM_VERSION}**",
        inline=False,
    )

    embed.add_field(
        name="🥊 Fighter Profiles",
        value=f"**{FIGHTER_PROFILE_VERSION}**",
        inline=False,
    )

    embed.add_field(
        name="🏢 Gym System",
        value=f"**{GYM_SYSTEM_VERSION}**",
        inline=False,
    )

    embed.add_field(
        name="🖼️ Gym Posters",
        value=f"**{GYM_POSTER_VERSION}**",
        inline=False,
    )

    embed.add_field(
        name="🛠️ Gym Management",
        value=f"**{GYM_MANAGEMENT_VERSION}**",
        inline=False,
    )

    embed.add_field(
        name="📚 Gym History",
        value=f"**{GYM_HISTORY_VERSION}**",
        inline=False,
    )
    embed.add_field(
        name="🧹 Gym Normalization",
        value=f"**{GYM_NORMALIZATION_VERSION}**",
        inline=False,
    )
    embed.add_field(
        name="🧹 Test Cleanup",
        value=f"**{CLEANUP_SYSTEM_VERSION}**",
        inline=False,
    )
    embed.add_field(
        name="💾 Database Backup",
        value=f"**{DATABASE_BACKUP_VERSION}**",
        inline=False,
    )
    embed.add_field(
        name="💰 Payout System",
        value=f"**{PAYOUT_SYSTEM_VERSION}**",
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
# OSBL AUTOMATIC FIGHT POSTER RENDERER
# Uses one locked master template per weight class.
# =========================================================

FIGHT_CARD_TEMPLATE_FILES = {
    "Lightweight": "osbl_lightweight_fight_card.png",
    "Middleweight": "osbl_middleweight_fight_card.png",
    "Heavyweight": "osbl_heavyweight_fight_card.png",
}

CHAMPIONSHIP_TEMPLATE_FILE = "osbl_championship_fight_card.png"


def _fight_card_asset_path(filename):
    return Path(__file__).resolve().parent / filename


def _load_osbl_font(size, bold=True):
    """Load a bold condensed-ish system font when available, with safe fallbacks."""
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ] if bold else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for font_path in candidates:
        try:
            return ImageFont.truetype(font_path, size=size)
        except OSError:
            pass
    # Pillow ships a scalable default font in modern versions. Railway images
    # may not include OS system fonts, so preserve the requested point size
    # instead of falling back to the old tiny fixed-size bitmap font.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Compatibility fallback for older Pillow builds.
        return ImageFont.load_default()


def _fit_font(draw, text, max_width, start_size, min_size=12):
    size = start_size
    while size >= min_size:
        font = _load_osbl_font(size, bold=True)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 2
    return _load_osbl_font(min_size, bold=True)


def _draw_centered(draw, xy_box, text, font, fill=(245, 245, 245), stroke=2, stroke_fill=(0, 0, 0)):
    x1, y1, x2, y2 = xy_box
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = x1 + ((x2 - x1) - tw) / 2
    y = y1 + ((y2 - y1) - th) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def _cover_value_area(draw, box, fill=(8, 8, 8)):
    """Cover baked placeholder text so live OSBL data can be redrawn cleanly."""
    draw.rectangle(box, fill=fill)


def _draw_panel(draw, box, fill=(8, 8, 8), outline=(214, 170, 72), width=2):
    """Draw a clean black/gold information panel over baked template text."""
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1, y1, x2, y2), radius=10, fill=fill, outline=outline, width=width)


def _paste_fighter_portrait(canvas, photo_bytes, box):
    from PIL import Image, ImageOps, ImageEnhance

    portrait = Image.open(io.BytesIO(bytes(photo_bytes))).convert("RGB")
    x1, y1, x2, y2 = box

    # Use one consistent crop rule for every fighter so portrait framing is
    # predictable across the whole league. Slightly favor the upper body/face.
    portrait = ImageOps.fit(
        portrait,
        (x2 - x1, y2 - y1),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.38),
    )
    portrait = ImageEnhance.Contrast(portrait).enhance(1.06)
    portrait = ImageEnhance.Sharpness(portrait).enhance(1.04)
    canvas.paste(portrait, (x1, y1))


def _render_osbl_championship_poster(booking, fighter1, fighter2):
    """Render the premium OSBL championship poster using the locked master template."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Pillow is not installed. Add Pillow to requirements.txt.") from exc

    template_path = _fight_card_asset_path(CHAMPIONSHIP_TEMPLATE_FILE)
    if not template_path.exists():
        raise RuntimeError(f"Missing championship template file: {CHAMPIONSHIP_TEMPLATE_FILE}")

    canvas = Image.open(template_path).convert("RGB")
    if canvas.size != (1024, 1536):
        canvas = canvas.resize((1024, 1536), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)

    # Always place the defending champion on the left when one exists.
    left, right = fighter1, fighter2
    if fighter2.get("champion") and not fighter1.get("champion"):
        left, right = fighter2, fighter1

    gold = (232, 188, 80)
    white = (252, 252, 252)
    black = (5, 5, 5)

    # Portrait windows stay inside the premium gold frame.
    _paste_fighter_portrait(canvas, left["photo_data"], (25, 367, 413, 711))
    _paste_fighter_portrait(canvas, right["photo_data"], (611, 367, 999, 711))

    # Fighter role bars. Covers baked sample wording and redraws live roles.
    left_role = "DEFENDING CHAMPION" if left.get("champion") else "TITLE CONTENDER"
    right_role = "CHALLENGER" if left.get("champion") else "TITLE CONTENDER"
    _draw_panel(draw, (36, 705, 401, 752), fill=black, outline=gold, width=3)
    _draw_panel(draw, (623, 705, 988, 752), fill=black, outline=gold, width=3)
    role_font_l = _fit_font(draw, left_role, 330, 25, 16)
    role_font_r = _fit_font(draw, right_role, 330, 25, 16)
    _draw_centered(draw, (48, 712, 389, 745), left_role, role_font_l, fill=gold, stroke=1)
    _draw_centered(draw, (635, 712, 976, 745), right_role, role_font_r, fill=gold, stroke=1)

    # Large fighter names.
    _draw_panel(draw, (24, 751, 414, 817), fill=black, outline=gold, width=3)
    _draw_panel(draw, (610, 751, 1000, 817), fill=black, outline=gold, width=3)
    left_name = str(left["fighter_name"]).upper()
    right_name = str(right["fighter_name"]).upper()
    lf = _fit_font(draw, left_name, 350, 38, 22)
    rf = _fit_font(draw, right_name, 350, 38, 22)
    _draw_centered(draw, (39, 759, 399, 808), left_name, lf, fill=white, stroke=2)
    _draw_centered(draw, (625, 759, 985, 808), right_name, rf, fill=white, stroke=2)

    # Championship stat panels.
    left_stats = (24, 817, 414, 908)
    right_stats = (610, 817, 1000, 908)
    for panel in (left_stats, right_stats):
        _draw_panel(draw, panel, fill=black, outline=gold, width=3)

    def draw_champ_stats(panel, fighter):
        x1, y1, x2, y2 = panel
        labels = ["RECORD", "RP", "PROGRESSION", "DIVISION RANK"]
        values = [
            f"{fighter['wins']}-{fighter['losses']}",
            str(fighter["rp"]),
            str(fighter["progression_rank"] or "--").upper(),
            f"#{fighter['division_rank']}" if fighter["division_rank"] else "--",
        ]
        widths = [0.22, 0.18, 0.37, 0.23]
        px = x1 + 6
        usable = (x2 - x1) - 12
        for i, (label, value, frac) in enumerate(zip(labels, values, widths)):
            cw = int(usable * frac)
            if i:
                draw.line((px, y1 + 10, px, y2 - 10), fill=(125, 98, 42), width=1)
            lfont = _fit_font(draw, label, cw - 6, 14, 10)
            vfont = _fit_font(draw, value, cw - 6, 24, 15)
            _draw_centered(draw, (px+2, y1+7, px+cw-2, y1+36), label, lfont, fill=gold, stroke=1)
            _draw_centered(draw, (px+2, y1+39, px+cw-2, y2-6), value, vfont, fill=white, stroke=1)
            px += cw

    draw_champ_stats(left_stats, left)
    draw_champ_stats(right_stats, right)

    # Rebuild the championship title strip so every division is dynamic.
    _draw_panel(draw, (94, 1000, 930, 1120), fill=black, outline=gold, width=4)
    title_text = "CHAMPIONSHIP FIGHT"
    title_font = _fit_font(draw, title_text, 760, 58, 34)
    _draw_centered(draw, (115, 1007, 909, 1067), title_text, title_font, fill=gold, stroke=2)
    division_text = f"{booking['division'].upper()} CHAMPIONSHIP"
    div_font = _fit_font(draw, division_text, 700, 32, 20)
    _draw_centered(draw, (150, 1064, 874, 1106), division_text, div_font, fill=white, stroke=1)

    # Audit marker only; keep it discreet.
    audit_font = _load_osbl_font(16, bold=True)
    draw.text((26, 1490), f"BOOKING #{booking['id']}", font=audit_font, fill=gold, stroke_width=1, stroke_fill=(0,0,0))

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def _render_osbl_fight_poster(booking, fighter1, fighter2):
    """Return a polished PNG BytesIO for an OSBL matchup."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Pillow is not installed. Add Pillow to requirements.txt.") from exc

    if booking["bout_type"] == "championship":
        return _render_osbl_championship_poster(booking, fighter1, fighter2)

    division = booking["division"]
    template_filename = FIGHT_CARD_TEMPLATE_FILES.get(division)
    if not template_filename:
        raise RuntimeError(f"No fight-card template is configured for {division}.")

    template_path = _fight_card_asset_path(template_filename)
    if not template_path.exists():
        raise RuntimeError(f"Missing template file: {template_filename}")

    canvas = Image.open(template_path).convert("RGB")
    if canvas.size != (1122, 1402):
        canvas = canvas.resize((1122, 1402), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)

    # Portrait windows. Keep these inside the existing gold frames.
    _paste_fighter_portrait(canvas, fighter1["photo_data"], (31, 372, 451, 891))
    _paste_fighter_portrait(canvas, fighter2["photo_data"], (671, 372, 1091, 891))

    # Rebuild the entire lower information area instead of depending on any
    # AI-baked placeholder text. This makes the live information readable on mobile.
    gold = (224, 180, 72)
    white = (250, 250, 250)
    muted = (190, 190, 190)
    black = (6, 6, 6)

    left_name = (25, 884, 458, 970)
    right_name = (664, 884, 1097, 970)
    for panel in (left_name, right_name):
        _draw_panel(draw, panel, fill=black, outline=gold, width=3)

    name1 = str(fighter1["fighter_name"]).upper()
    name2 = str(fighter2["fighter_name"]).upper()
    font1 = _fit_font(draw, name1, 392, 52, 28)
    font2 = _fit_font(draw, name2, 392, 52, 28)
    _draw_centered(draw, (38, 895, 445, 958), name1, font1, fill=white, stroke=2)
    _draw_centered(draw, (677, 895, 1084, 958), name2, font2, fill=white, stroke=2)

    # Full stat panels with our own labels and values.
    left_stats = (25, 969, 458, 1085)
    right_stats = (664, 969, 1097, 1085)
    for panel in (left_stats, right_stats):
        _draw_panel(draw, panel, fill=black, outline=gold, width=3)

    def draw_stat_row(panel, fighter):
        x1, y1, x2, y2 = panel
        widths = [0.23, 0.18, 0.36, 0.23]
        labels = ["RECORD", "RP", "PROGRESSION", "DIVISION RANK"]
        values = [
            f"{fighter['wins']}-{fighter['losses']}",
            str(fighter["rp"]),
            str(fighter["progression_rank"] or "--").upper(),
            f"#{fighter['division_rank']}" if fighter["division_rank"] else "--",
        ]
        px = x1 + 8
        usable = (x2 - x1) - 16
        for i, (frac, label, value) in enumerate(zip(widths, labels, values)):
            cw = int(usable * frac)
            cell = (px, y1 + 6, px + cw, y2 - 6)
            if i:
                draw.line((px, y1 + 12, px, y2 - 12), fill=(115, 91, 40), width=1)
            label_font = _fit_font(draw, label, cw - 8, 16, 11)
            value_font = _fit_font(draw, value, cw - 8, 27, 16)
            _draw_centered(draw, (cell[0]+2, y1+10, cell[2]-2, y1+43), label, label_font, fill=gold, stroke=1)
            _draw_centered(draw, (cell[0]+2, y1+45, cell[2]-2, y2-8), value, value_font, fill=white, stroke=1)
            px += cw

    draw_stat_row(left_stats, fighter1)
    draw_stat_row(right_stats, fighter2)

    # Completely cover and redraw the division/bout panel.
    division_panel = (279, 1091, 843, 1182)
    _draw_panel(draw, division_panel, fill=black, outline=gold, width=4)
    division_text = f"{division.upper()} DIVISION"
    division_font = _fit_font(draw, division_text, 520, 40, 25)
    _draw_centered(draw, (297, 1098, 825, 1144), division_text, division_font, fill=gold, stroke=2)

    bout_text = "CHAMPIONSHIP BOUT" if booking["bout_type"] == "championship" else "REGULAR BOUT"
    bout_font = _fit_font(draw, bout_text, 390, 24, 16)
    _draw_centered(draw, (360, 1143, 762, 1175), bout_text, bout_font, fill=white, stroke=1)

    # Booking ID remains small for commissioner audit/troubleshooting.
    booking_text = f"BOOKING #{booking['id']}"
    booking_font = _load_osbl_font(18, bold=True)
    draw.text((18, 1365), booking_text, font=booking_font, fill=gold, stroke_width=1, stroke_fill=(0, 0, 0))

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


async def _send_locked_fight_poster(ctx, booking_id):
    async with bot.db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT b.*,
                   f1.fighter_name AS f1_name, f1.wins AS f1_wins, f1.losses AS f1_losses,
                   f1.rp AS f1_rp, f1.progression_rank AS f1_progression,
                   f1.division_rank AS f1_division_rank, f1.champion AS f1_champion,
                   f1.title_defenses AS f1_title_defenses, f1.photo_data AS f1_photo,
                   f2.fighter_name AS f2_name, f2.wins AS f2_wins, f2.losses AS f2_losses,
                   f2.rp AS f2_rp, f2.progression_rank AS f2_progression,
                   f2.division_rank AS f2_division_rank, f2.champion AS f2_champion,
                   f2.title_defenses AS f2_title_defenses, f2.photo_data AS f2_photo
            FROM fight_bookings b
            JOIN fighters f1 ON f1.fighter_key = b.fighter1_key
            JOIN fighters f2 ON f2.fighter_key = b.fighter2_key
            WHERE b.id = $1
            """,
            booking_id,
        )

    if not row:
        await ctx.send(f"❌ Booking ID **{booking_id}** was not found.")
        return False
    if not row["f1_photo"] or not row["f2_photo"]:
        await ctx.send(
            "🖼️ **FIGHT POSTER NOT GENERATED**\n"
            "Both fighters need locked portraits first. Use `!setfighterphoto Fighter Name`."
        )
        return False

    fighter1 = {
        "fighter_name": row["f1_name"], "wins": row["f1_wins"], "losses": row["f1_losses"],
        "rp": row["f1_rp"], "progression_rank": row["f1_progression"],
        "division_rank": row["f1_division_rank"], "champion": row["f1_champion"],
        "title_defenses": row["f1_title_defenses"], "photo_data": row["f1_photo"],
    }
    fighter2 = {
        "fighter_name": row["f2_name"], "wins": row["f2_wins"], "losses": row["f2_losses"],
        "rp": row["f2_rp"], "progression_rank": row["f2_progression"],
        "division_rank": row["f2_division_rank"], "champion": row["f2_champion"],
        "title_defenses": row["f2_title_defenses"], "photo_data": row["f2_photo"],
    }

    try:
        poster = _render_osbl_fight_poster(row, fighter1, fighter2)
    except Exception as exc:
        print(f"Fight poster generation error for booking {booking_id}: {type(exc).__name__}: {exc}")
        await ctx.send(
            "⚠️ **MATCHUP LOCKED, BUT POSTER COULD NOT BE GENERATED**\n"
            f"`{type(exc).__name__}: {exc}`"
        )
        return False

    filename = f"osbl_booking_{booking_id}_{row['division'].casefold()}_fight_card.png"
    poster_file = discord.File(poster, filename=filename)
    embed = discord.Embed(
        title="🥊 OSBL OFFICIAL FIGHT CARD",
        description=(
            f"**{row['fighter1_name']} vs {row['fighter2_name']}**\n"
            f"{row['division']} • {row['bout_type'].title()} • Booking #{booking_id}\n"
            "🔒 **OFFICIAL MATCHUP LOCKED**\n"
            f"🎨 Renderer: **{POSTER_RENDERER_VERSION}**"
        ),
        color=discord.Color.gold(),
    )
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text="ONE LEAGUE. ONE STANDARD. ONE CHAMPION.")
    await ctx.send(embed=embed, file=poster_file)
    return True


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def fightposter(ctx, booking_id: int = None):
    if booking_id is None:
        await ctx.send("❌ Use `!fightposter <Booking ID>`")
        return
    await _send_locked_fight_poster(ctx, booking_id)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def fightposterall(ctx):
    """Generate official posters for every currently locked matchup."""
    async with bot.db.acquire() as conn:
        bookings = await conn.fetch(
            """
            SELECT id
            FROM fight_bookings
            WHERE status = 'locked'
            ORDER BY id ASC
            """
        )

    if not bookings:
        await ctx.send(
            "📭 **NO LOCKED MATCHUPS**\n"
            "There are no locked fights available for poster generation."
        )
        return

    await ctx.send(
        f"🎨 **OSBL FIGHT POSTER BATCH**\nGenerating **{len(bookings)}** locked matchup poster(s)..."
    )

    generated = 0
    failed = 0
    for booking in bookings:
        ok = await _send_locked_fight_poster(ctx, booking["id"])
        if ok:
            generated += 1
        else:
            failed += 1

    await ctx.send(
        "✅ **POSTER BATCH COMPLETE**\n"
        f"Generated: **{generated}**\n"
        f"Skipped/Failed: **{failed}**"
    )


# =========================================================
# OSBL MATCHMAKING SYSTEM
# Books, validates, locks, and displays upcoming matchups.
# =========================================================

async def _matchup_snapshot(fighter1_name, fighter2_name):
    fighter1_key = fighter1_name.casefold().strip()
    fighter2_key = fighter2_name.casefold().strip()

    if fighter1_key == fighter2_key:
        return None, None, "❌ A fighter cannot be matched against themselves."

    async with bot.db.acquire() as conn:
        fighter1 = await conn.fetchrow(
            "SELECT * FROM fighters WHERE fighter_key = $1",
            fighter1_key,
        )
        fighter2 = await conn.fetchrow(
            "SELECT * FROM fighters WHERE fighter_key = $1",
            fighter2_key,
        )

    if not fighter1:
        return None, None, f"❌ **{fighter1_name}** is not registered in OSBL."
    if not fighter2:
        return None, None, f"❌ **{fighter2_name}** is not registered in OSBL."
    if fighter1["division"] != fighter2["division"]:
        return fighter1, fighter2, "❌ Fighters must be in the same division."

    return fighter1, fighter2, None


def _championship_booking_eligibility(fighter1, fighter2):
    f1_champ = bool(fighter1["champion"])
    f2_champ = bool(fighter2["champion"])

    if f1_champ and f2_champ:
        return False, "Both fighters are marked as champions. Run `!systemcheck` before sanctioning."

    if f1_champ or f2_champ:
        challenger = fighter2 if f1_champ else fighter1
        if challenger["rp"] < 140:
            return (
                False,
                f"**{challenger['fighter_name']}** has {challenger['rp']} RP. "
                "OSBL title eligibility begins at **140 RP**.",
            )
        return (
            True,
            "Champion vs title-eligible challenger. Commissioner sanction is still required; "
            "140+ RP does not guarantee a title shot.",
        )

    if fighter1["rp"] >= 140 and fighter2["rp"] >= 140:
        return (
            True,
            "Vacant-title eligibility check passed: both fighters have 140+ RP. "
            "Commissioner sanction is still required.",
        )

    return (
        False,
        "No active champion is in this matchup, and both fighters are not at 140+ RP.",
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def matchupcheck(ctx, *, details: str = None):
    if not details:
        await ctx.send(
            "❌ **MATCHUP CHECK FORMAT**\n"
            "`!matchupcheck Fighter One | Fighter Two`"
        )
        return

    parts = [part.strip() for part in details.split("|")]
    if len(parts) != 2:
        await ctx.send("❌ Use exactly: `!matchupcheck Fighter One | Fighter Two`")
        return

    fighter1, fighter2, error = await _matchup_snapshot(parts[0], parts[1])
    if error:
        await ctx.send(error)
        return

    champ_ok, champ_reason = _championship_booking_eligibility(fighter1, fighter2)

    embed = discord.Embed(
        title="🔎 OSBL MATCHUP CHECK",
        description=f"**{fighter1['division']} Division**",
        color=discord.Color.green(),
    )
    embed.add_field(
        name=f"🥊 {fighter1['fighter_name']}",
        value=(
            f"Record: **{fighter1['wins']}-{fighter1['losses']}**\n"
            f"RP: **{fighter1['rp']}**\n"
            f"Progression: **{fighter1['progression_rank']}**\n"
            f"Division Rank: **{fighter1['division_rank'] or 'Unranked'}**\n"
            f"Champion: **{'YES' if fighter1['champion'] else 'NO'}**"
        ),
        inline=False,
    )
    embed.add_field(
        name=f"🥊 {fighter2['fighter_name']}",
        value=(
            f"Record: **{fighter2['wins']}-{fighter2['losses']}**\n"
            f"RP: **{fighter2['rp']}**\n"
            f"Progression: **{fighter2['progression_rank']}**\n"
            f"Division Rank: **{fighter2['division_rank'] or 'Unranked'}**\n"
            f"Champion: **{'YES' if fighter2['champion'] else 'NO'}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="✅ Regular Fight",
        value="Eligible — same division.",
        inline=False,
    )
    embed.add_field(
        name="🏆 Championship Fight",
        value=("✅ Eligible for sanction — " if champ_ok else "❌ Not currently eligible — ") + champ_reason,
        inline=False,
    )
    embed.set_footer(text="Read-only eligibility check • Final matchmaking authority remains with OSBL officials")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def bookfight(ctx, *, details: str = None):
    if not details:
        await ctx.send(
            "❌ **BOOK FIGHT FORMAT**\n"
            "Regular: `!bookfight Fighter One | Fighter Two`\n"
            "Championship: `!bookfight Fighter One | Fighter Two | championship`"
        )
        return

    parts = [part.strip() for part in details.split("|")]
    if len(parts) not in (2, 3):
        await ctx.send(
            "❌ Use `!bookfight Fighter One | Fighter Two` or "
            "`!bookfight Fighter One | Fighter Two | championship`"
        )
        return

    fighter1, fighter2, error = await _matchup_snapshot(parts[0], parts[1])
    if error:
        await ctx.send(error)
        return

    bout_type = "regular"
    if len(parts) == 3:
        requested = parts[2].casefold()
        if requested not in ("regular", "championship", "title"):
            await ctx.send("❌ Bout type must be **regular** or **championship**.")
            return
        bout_type = "championship" if requested in ("championship", "title") else "regular"

    if bout_type == "championship":
        champ_ok, champ_reason = _championship_booking_eligibility(fighter1, fighter2)
        if not champ_ok:
            await ctx.send(f"❌ **CHAMPIONSHIP BOOKING BLOCKED**\n{champ_reason}")
            return

    async with bot.db.acquire() as conn:
        conflict = await conn.fetchrow(
            """
            SELECT id, fighter1_name, fighter2_name, status
            FROM fight_bookings
            WHERE status IN ('booked', 'locked')
              AND (
                    fighter1_key = ANY($1::text[])
                 OR fighter2_key = ANY($1::text[])
              )
            ORDER BY id DESC
            LIMIT 1
            """,
            [fighter1["fighter_key"], fighter2["fighter_key"]],
        )
        if conflict:
            await ctx.send(
                "⚠️ **BOOKING CONFLICT**\n"
                f"Booking ID **{conflict['id']}** is already {conflict['status']}: "
                f"**{conflict['fighter1_name']} vs {conflict['fighter2_name']}**.\n"
                "Cancel or complete that booking before creating another for either fighter."
            )
            return

        booking = await conn.fetchrow(
            """
            INSERT INTO fight_bookings (
                fighter1_key, fighter1_name,
                fighter2_key, fighter2_name,
                division, bout_type,
                booked_by_id, booked_by_name
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, booked_at
            """,
            fighter1["fighter_key"], fighter1["fighter_name"],
            fighter2["fighter_key"], fighter2["fighter_name"],
            fighter1["division"], bout_type,
            ctx.author.id, ctx.author.display_name,
        )

    embed = discord.Embed(
        title="📋 OSBL MATCHUP BOOKED",
        description=f"Booking ID **{booking['id']}**",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="🥊 Matchup",
        value=f"**{fighter1['fighter_name']} vs {fighter2['fighter_name']}**",
        inline=False,
    )
    embed.add_field(name="Division", value=fighter1["division"], inline=True)
    embed.add_field(name="Bout Type", value=bout_type.title(), inline=True)
    embed.add_field(name="Status", value="🟡 BOOKED — not locked", inline=False)
    embed.add_field(
        name="Next Step",
        value=f"Commissioner: `!lockfight {booking['id']}`",
        inline=False,
    )
    embed.set_footer(text=f"Booked by {ctx.author.display_name}")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def lockfight(ctx, booking_id: int = None):
    if booking_id is None:
        await ctx.send("❌ Use `!lockfight <Booking ID>`")
        return

    async with bot.db.acquire() as conn:
        async with conn.transaction():
            booking = await conn.fetchrow(
                "SELECT * FROM fight_bookings WHERE id = $1 FOR UPDATE",
                booking_id,
            )
            if not booking:
                await ctx.send(f"❌ Booking ID **{booking_id}** was not found.")
                return
            if booking["status"] == "cancelled":
                await ctx.send("❌ This booking has been cancelled.")
                return
            if booking["status"] == "locked":
                await ctx.send(f"🔒 Booking ID **{booking_id}** is already locked.")
                return

            session_id = await conn.fetchval(
                """
                SELECT id FROM fight_night_sessions
                WHERE status = 'active'
                ORDER BY id DESC LIMIT 1
                """
            )

            await conn.execute(
                """
                UPDATE fight_bookings
                SET status = 'locked',
                    fight_night_session_id = COALESCE($1, fight_night_session_id),
                    locked_by_id = $2,
                    locked_by_name = $3,
                    locked_at = NOW()
                WHERE id = $4
                """,
                session_id,
                ctx.author.id,
                ctx.author.display_name,
                booking_id,
            )

    session_text = f"Fight Night Session **{session_id}**" if session_id else "Next Fight Night (unassigned until session opens)"
    await ctx.send(
        f"🔒 **MATCHUP LOCKED**\n"
        f"Booking ID: **{booking_id}**\n"
        f"**{booking['fighter1_name']} vs {booking['fighter2_name']}**\n"
        f"{booking['division']} • {booking['bout_type'].title()}\n"
        f"Assigned to: **{session_text}**"
    )

    # Automatically generate the division-specific visual card when both portraits are locked.
    await _send_locked_fight_poster(ctx, booking_id)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def cancelbookedfight(ctx, booking_id: int = None):
    if booking_id is None:
        await ctx.send("❌ Use `!cancelbookedfight <Booking ID>`")
        return

    async with bot.db.acquire() as conn:
        booking = await conn.fetchrow(
            "SELECT * FROM fight_bookings WHERE id = $1",
            booking_id,
        )
        if not booking:
            await ctx.send(f"❌ Booking ID **{booking_id}** was not found.")
            return
        if booking["status"] == "cancelled":
            await ctx.send(f"⚠️ Booking ID **{booking_id}** is already cancelled.")
            return

        await conn.execute(
            """
            UPDATE fight_bookings
            SET status = 'cancelled',
                cancelled_by_id = $1,
                cancelled_by_name = $2,
                cancelled_at = NOW()
            WHERE id = $3
            """,
            ctx.author.id,
            ctx.author.display_name,
            booking_id,
        )

    await ctx.send(
        f"🚫 **MATCHUP CANCELLED**\n"
        f"Booking ID: **{booking_id}**\n"
        f"**{booking['fighter1_name']} vs {booking['fighter2_name']}**\n"
        f"Cancelled by **{ctx.author.display_name}**."
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def fightcard(ctx):
    async with bot.db.acquire() as conn:
        active_session = await conn.fetchval(
            "SELECT id FROM fight_night_sessions WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        )
        rows = await conn.fetch(
            """
            SELECT
                b.*,
                f1.photo_data AS fighter1_photo_data,
                f1.photo_filename AS fighter1_photo_filename,
                f2.photo_data AS fighter2_photo_data,
                f2.photo_filename AS fighter2_photo_filename
            FROM fight_bookings b
            LEFT JOIN fighters f1 ON f1.fighter_key = b.fighter1_key
            LEFT JOIN fighters f2 ON f2.fighter_key = b.fighter2_key
            WHERE b.status IN ('booked', 'locked')
            ORDER BY CASE WHEN b.status = 'locked' THEN 0 ELSE 1 END, b.id ASC
            LIMIT 20
            """
        )

    if not rows:
        await ctx.send("📋 **OSBL FIGHT CARD**\nNo active booked matchups.")
        return

    lines = []
    for row in rows:
        status_icon = "🔒" if row["status"] == "locked" else "🟡"
        session_text = (
            f"Session {row['fight_night_session_id']}"
            if row["fight_night_session_id"]
            else "Unassigned"
        )
        photo_icon = "📸" if row["fighter1_photo_data"] and row["fighter2_photo_data"] else "🖼️"
        lines.append(
            f"**#{row['id']}** {status_icon} **{row['fighter1_name']} vs {row['fighter2_name']}** {photo_icon}\n"
            f"{row['division']} • {row['bout_type'].title()} • {session_text}"
        )

    embed = discord.Embed(
        title="🥊 OSBL OFFICIAL FIGHT CARD",
        description="\n\n".join(lines),
        color=discord.Color.gold(),
    )
    if active_session:
        embed.add_field(
            name="🟢 Active Fight Night",
            value=f"Session **{active_session}**",
            inline=False,
        )
    embed.set_footer(text="🟡 Booked • 🔒 Locked • 📸 Both fighter portraits locked")
    await ctx.send(embed=embed)

    # Send locked fighter portraits for each matchup. Images are stored in PostgreSQL,
    # so they survive Railway redeploys and do not depend on temporary Discord URLs.
    for row in rows:
        portrait_embeds = []
        files = []

        def safe_ext(filename):
            filename = filename or "fighter.png"
            ext = os.path.splitext(filename)[1].lower()
            return ext if ext in {".png", ".jpg", ".jpeg", ".webp"} else ".png"

        if row["fighter1_photo_data"]:
            filename1 = f"booking_{row['id']}_fighter1{safe_ext(row['fighter1_photo_filename'])}"
            files.append(discord.File(io.BytesIO(bytes(row["fighter1_photo_data"])), filename=filename1))
            e1 = discord.Embed(
                title=f"🥊 {row['fighter1_name']}",
                description=f"Booking #{row['id']} • {row['division']} • {row['bout_type'].title()}",
                color=discord.Color.gold(),
            )
            e1.set_image(url=f"attachment://{filename1}")
            portrait_embeds.append(e1)

        if row["fighter2_photo_data"]:
            filename2 = f"booking_{row['id']}_fighter2{safe_ext(row['fighter2_photo_filename'])}"
            files.append(discord.File(io.BytesIO(bytes(row["fighter2_photo_data"])), filename=filename2))
            e2 = discord.Embed(
                title=f"🥊 {row['fighter2_name']}",
                description=f"Booking #{row['id']} • {row['division']} • {row['bout_type'].title()}",
                color=discord.Color.gold(),
            )
            e2.set_image(url=f"attachment://{filename2}")
            portrait_embeds.append(e2)

        if portrait_embeds:
            await ctx.send(
                content=f"📸 **OFFICIAL MATCHUP PORTRAITS — Booking #{row['id']}**",
                embeds=portrait_embeds,
                files=files,
            )


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

            assigned_bookings = await conn.fetchval(
                """
                WITH assigned AS (
                    UPDATE fight_bookings
                    SET fight_night_session_id = $1
                    WHERE status = 'locked'
                      AND fight_night_session_id IS NULL
                    RETURNING id
                )
                SELECT COUNT(*) FROM assigned
                """,
                session["id"],
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
        name="Locked Fight Card",
        value=f"**{assigned_bookings}** locked matchup(s) assigned to this session",
        inline=False,
    )
    embed.add_field(
        name="Commissioner Commands",
        value="`!fightnightstatus` • `!fightcard` • `!endfightnight`",
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
# FIGHTER PHOTO REGISTRY
# Permanent portrait storage in PostgreSQL
# =========================================================

ALLOWED_FIGHTER_PHOTO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_FIGHTER_PHOTO_BYTES = 4 * 1024 * 1024


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def setfighterphoto(ctx, *, fighter_name: str = None):
    if not fighter_name:
        await ctx.send(
            "❌ **PHOTO FORMAT**\n"
            "Attach one fighter image, then use:\n"
            "`!setfighterphoto Fighter Name`"
        )
        return

    if len(ctx.message.attachments) != 1:
        await ctx.send(
            "❌ Attach **exactly one** fighter image to the same message as "
            "`!setfighterphoto Fighter Name`."
        )
        return

    attachment = ctx.message.attachments[0]
    ext = os.path.splitext(attachment.filename or "")[1].lower()
    content_type = (attachment.content_type or "").lower()

    if ext not in ALLOWED_FIGHTER_PHOTO_EXTENSIONS and not content_type.startswith("image/"):
        await ctx.send("❌ Fighter photos must be PNG, JPG/JPEG, or WEBP images.")
        return

    if attachment.size and attachment.size > MAX_FIGHTER_PHOTO_BYTES:
        await ctx.send("❌ Fighter photo is too large. Maximum size is **4 MB**.")
        return

    fighter_key = fighter_name.casefold().strip()
    fighter = await bot.db.fetchrow(
        "SELECT fighter_name FROM fighters WHERE fighter_key = $1",
        fighter_key,
    )
    if not fighter:
        await ctx.send(f"❌ **{fighter_name}** is not registered in OSBL.")
        return

    try:
        photo_bytes = await attachment.read()
    except Exception:
        await ctx.send("❌ I could not read that image attachment. Please try attaching it again.")
        return

    if not photo_bytes or len(photo_bytes) > MAX_FIGHTER_PHOTO_BYTES:
        await ctx.send("❌ Fighter photo could not be saved or exceeds the **4 MB** limit.")
        return

    await bot.db.execute(
        """
        UPDATE fighters
        SET photo_data = $1,
            photo_filename = $2,
            photo_content_type = $3,
            photo_updated_at = NOW(),
            photo_locked_by_id = $4,
            photo_locked_by_name = $5,
            updated_at = NOW()
        WHERE fighter_key = $6
        """,
        photo_bytes,
        attachment.filename,
        attachment.content_type or "application/octet-stream",
        ctx.author.id,
        ctx.author.display_name,
        fighter_key,
    )

    preview_name = f"fighter_photo{ext if ext in ALLOWED_FIGHTER_PHOTO_EXTENSIONS else '.png'}"
    preview = discord.File(io.BytesIO(photo_bytes), filename=preview_name)
    embed = discord.Embed(
        title="🔒 OSBL FIGHTER PHOTO LOCKED",
        description=f"**{fighter['fighter_name']}** now has an official locked character portrait.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Locked By", value=ctx.author.display_name, inline=False)
    embed.add_field(
        name="Fight Card",
        value="This portrait will automatically appear when the fighter is on `!fightcard`.",
        inline=False,
    )
    embed.set_image(url=f"attachment://{preview_name}")
    await ctx.send(embed=embed, file=preview)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def fighterphoto(ctx, *, fighter_name: str = None):
    if not fighter_name:
        await ctx.send("❌ Use `!fighterphoto Fighter Name`")
        return

    fighter = await bot.db.fetchrow(
        """
        SELECT fighter_name, division, gym, photo_data, photo_filename,
               photo_updated_at, photo_locked_by_name
        FROM fighters
        WHERE fighter_key = $1
        """,
        fighter_name.casefold().strip(),
    )
    if not fighter:
        await ctx.send(f"❌ **{fighter_name}** is not registered in OSBL.")
        return
    if not fighter["photo_data"]:
        await ctx.send(f"🖼️ **{fighter['fighter_name']}** does not have a locked fighter photo yet.")
        return

    ext = os.path.splitext(fighter["photo_filename"] or "")[1].lower()
    if ext not in ALLOWED_FIGHTER_PHOTO_EXTENSIONS:
        ext = ".png"
    filename = f"fighter_photo{ext}"
    photo_file = discord.File(io.BytesIO(bytes(fighter["photo_data"])), filename=filename)
    embed = discord.Embed(
        title="📸 OSBL OFFICIAL FIGHTER PHOTO",
        description=f"**{fighter['fighter_name']}**\n{fighter['division']} • {fighter['gym']}",
        color=discord.Color.gold(),
    )
    if fighter["photo_locked_by_name"]:
        embed.set_footer(text=f"Locked by {fighter['photo_locked_by_name']}")
    embed.set_image(url=f"attachment://{filename}")
    await ctx.send(embed=embed, file=photo_file)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def removefighterphoto(ctx, *, fighter_name: str = None):
    if not fighter_name:
        await ctx.send("❌ Use `!removefighterphoto Fighter Name`")
        return

    fighter_key = fighter_name.casefold().strip()
    fighter = await bot.db.fetchrow(
        "SELECT fighter_name, photo_data FROM fighters WHERE fighter_key = $1",
        fighter_key,
    )
    if not fighter:
        await ctx.send(f"❌ **{fighter_name}** is not registered in OSBL.")
        return
    if not fighter["photo_data"]:
        await ctx.send(f"⚠️ **{fighter['fighter_name']}** does not currently have a locked photo.")
        return

    await bot.db.execute(
        """
        UPDATE fighters
        SET photo_data = NULL,
            photo_filename = NULL,
            photo_content_type = NULL,
            photo_updated_at = NULL,
            photo_locked_by_id = NULL,
            photo_locked_by_name = NULL,
            updated_at = NOW()
        WHERE fighter_key = $1
        """,
        fighter_key,
    )
    await ctx.send(f"🗑️ Official fighter photo removed for **{fighter['fighter_name']}**.")


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

    # Store official gyms under one canonical name while still allowing
    # test gyms / independent promotions that are intentionally non-official.
    resolved_gym = _resolve_official_gym(gym)
    if resolved_gym:
        gym = resolved_gym

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
# OSBL FIGHTER PROFILE CARDS
# COMMANDS:
# !fighter Fighter Name
# !fightercard Fighter Name
# !profile Fighter Name
# =========================================================


def _profile_title_eligibility(row):
    if row["champion"]:
        return "👑 **CURRENT CHAMPION**"

    rp = int(row["rp"] or 0)
    if rp >= 140:
        return "🏆 **TITLE ELIGIBLE**"

    return f"🔒 **{140 - rp} RP** away from Title Eligibility"


def _profile_gym_name(gym):
    gym = (gym or "").strip()
    return gym if gym else "Independent / No Gym Assigned"


FIGHTER_PROFILE_TEMPLATE_FILE = "osbl_official_fighter_card.png"


def _render_osbl_fighter_profile_card(row, recent_results):
    """Render the official OSBL fighter profile card as a Discord-ready PNG."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Pillow is not installed. Add Pillow to requirements.txt.") from exc

    template_path = _fight_card_asset_path(FIGHTER_PROFILE_TEMPLATE_FILE)
    if not template_path.exists():
        raise RuntimeError(f"Missing fighter profile template file: {FIGHTER_PROFILE_TEMPLATE_FILE}")

    canvas = Image.open(template_path).convert("RGB")
    if canvas.size != (1122, 1402):
        canvas = canvas.resize((1122, 1402), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)

    gold = (232, 188, 80)
    white = (250, 250, 250)
    muted = (205, 205, 205)
    black = (6, 6, 6)
    green = (58, 220, 88)

    # Live fighter portrait. If no photo is locked, preserve the template silhouette.
    if row["photo_data"]:
        _paste_fighter_portrait(canvas, row["photo_data"], (112, 331, 532, 919))

    fighter_name = str(row["fighter_name"] or "FIGHTER").upper()
    division = str(row["division"] or "UNASSIGNED").upper()
    gym = _profile_gym_name(row["gym"])
    progression = str(row["progression_rank"] or "Prospect").upper()
    record = f"{int(row['wins'] or 0)}-{int(row['losses'] or 0)}"
    rp = int(row["rp"] or 0)
    earnings = int(row["career_earnings"] or 0)
    defenses = int(row["title_defenses"] or 0)
    if row["champion"]:
        rank_text = "CHAMPION"
        status_text = "CHAMPION"
        status_fill = gold
        eligibility_text = "CURRENT CHAMPION"
        eligibility_value = 140
    else:
        rank_text = f"#{row['division_rank']}" if row["division_rank"] else "UNRANKED"
        status_text = "ACTIVE"
        status_fill = green
        if rp >= 140:
            eligibility_text = "TITLE ELIGIBLE"
            eligibility_value = 140
        else:
            eligibility_text = f"{140-rp} RP AWAY"
            eligibility_value = max(0, rp)

    # Cover the baked right-side placeholders and redraw live league data.
    _draw_panel(draw, (548, 323, 991, 421), fill=black, outline=gold, width=3)
    name_font = _fit_font(draw, fighter_name, 405, 48, 25)
    _draw_centered(draw, (567, 337, 974, 389), fighter_name, name_font, fill=gold, stroke=2)
    sub_font = _fit_font(draw, "OFFICIAL OSBL FIGHTER", 390, 20, 13)
    _draw_centered(draw, (570, 389, 970, 414), "OFFICIAL OSBL FIGHTER", sub_font, fill=muted, stroke=1)

    # Gym and division panels.
    for panel in ((548, 433, 765, 534), (775, 433, 991, 534)):
        _draw_panel(draw, panel, fill=black, outline=gold, width=3)
    label_font = _load_osbl_font(16, bold=True)
    draw.text((570, 446), "GYM", font=label_font, fill=gold, stroke_width=1, stroke_fill=black)
    gym_font = _fit_font(draw, gym.upper(), 180, 22, 13)
    _draw_centered(draw, (565, 470, 750, 522), gym.upper(), gym_font, fill=white, stroke=1)
    draw.text((797, 446), "DIVISION", font=label_font, fill=gold, stroke_width=1, stroke_fill=black)
    div_font = _fit_font(draw, division, 175, 22, 13)
    _draw_centered(draw, (790, 470, 978, 522), division, div_font, fill=white, stroke=1)

    # Six live stat boxes.
    stat_panels = [
        ((548, 544, 691, 638), "RECORD", record),
        ((699, 544, 839, 638), "RP", str(rp)),
        ((847, 544, 991, 638), "PROGRESSION", progression),
        ((548, 648, 765, 742), "DIVISION RANK", rank_text),
        ((775, 648, 991, 742), "CAREER EARNINGS", f"${earnings:,}"),
        ((548, 752, 765, 846), "TITLE DEFENSES", str(defenses)),
    ]
    for panel, label, value in stat_panels:
        _draw_panel(draw, panel, fill=black, outline=gold, width=3)
        x1, y1, x2, y2 = panel
        lf = _fit_font(draw, label, (x2-x1)-18, 15, 10)
        vf = _fit_font(draw, value, (x2-x1)-18, 25, 13)
        _draw_centered(draw, (x1+8, y1+8, x2-8, y1+35), label, lf, fill=gold, stroke=1)
        _draw_centered(draw, (x1+8, y1+38, x2-8, y2-8), value, vf, fill=white, stroke=1)

    _draw_panel(draw, (775, 752, 991, 846), fill=black, outline=gold, width=3)
    _draw_centered(draw, (790, 760, 976, 789), "STATUS", _load_osbl_font(15, bold=True), fill=gold, stroke=1)
    sf = _fit_font(draw, status_text, 175, 27, 15)
    _draw_centered(draw, (790, 791, 976, 835), status_text, sf, fill=status_fill, stroke=1)

    # 140 RP title-eligibility bar.
    _draw_panel(draw, (548, 856, 991, 938), fill=black, outline=gold, width=3)
    head_font = _fit_font(draw, "140 RP TITLE ELIGIBILITY", 390, 20, 13)
    _draw_centered(draw, (570, 861, 970, 887), "140 RP TITLE ELIGIBILITY", head_font, fill=white, stroke=1)
    bar_x1, bar_y1, bar_x2, bar_y2 = 595, 896, 858, 916
    draw.rounded_rectangle((bar_x1, bar_y1, bar_x2, bar_y2), radius=6, fill=(35,35,35), outline=gold, width=2)
    ratio = min(1.0, max(0.0, eligibility_value / 140.0))
    if ratio > 0:
        fill_x = bar_x1 + int((bar_x2-bar_x1) * ratio)
        draw.rounded_rectangle((bar_x1, bar_y1, fill_x, bar_y2), radius=6, fill=gold)
    prog_text = "140 / 140" if row["champion"] or rp >= 140 else f"{rp} / 140"
    draw.text((872, 894), prog_text, font=_load_osbl_font(15, bold=True), fill=white, stroke_width=1, stroke_fill=black)
    elig_font = _fit_font(draw, eligibility_text, 365, 16, 11)
    _draw_centered(draw, (595, 918, 970, 936), eligibility_text, elig_font, fill=gold, stroke=1)

    # Recent results strip: render up to three official fights.
    _draw_panel(draw, (111, 948, 991, 1137), fill=black, outline=gold, width=3)
    _draw_centered(draw, (135, 955, 966, 991), "RECENT RESULTS", _load_osbl_font(23, bold=True), fill=gold, stroke=1)
    y = 1004
    if recent_results:
        for result in recent_results[:3]:
            rf = _fit_font(draw, result.replace("**", ""), 810, 18, 11)
            _draw_centered(draw, (145, y, 958, y+35), result.replace("**", ""), rf, fill=white, stroke=1)
            y += 43
    else:
        _draw_centered(draw, (145, 1020, 958, 1080), "NO OFFICIAL FIGHTS RECORDED YET", _load_osbl_font(18, bold=True), fill=muted, stroke=1)

    # Small version marker for deployment verification.
    vf = _load_osbl_font(13, bold=True)
    draw.text((114, 1150), FIGHTER_PROFILE_VERSION, font=vf, fill=gold, stroke_width=1, stroke_fill=black)

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


async def _recent_fighter_results(fighter_key, limit=3):
    rows = await bot.db.fetch(
        """
        SELECT fight_type, winner_key, loser_key, score, created_at
        FROM fight_history
        WHERE undone = FALSE
          AND (winner_key = $1 OR loser_key = $1)
        ORDER BY id DESC
        LIMIT $2
        """,
        fighter_key,
        int(limit),
    )

    results = []
    for fight in rows:
        won = fight["winner_key"] == fighter_key
        opponent_key = fight["loser_key"] if won else fight["winner_key"]
        opponent_name = await bot.db.fetchval(
            "SELECT fighter_name FROM fighters WHERE fighter_key = $1",
            opponent_key,
        )
        opponent_name = opponent_name or opponent_key
        label = "W" if won else "L"
        fight_type = str(fight["fight_type"] or "Regular").title()
        results.append(
            f"**{label}** vs **{opponent_name}** • {fight['score']} • {fight_type}"
        )

    return results


@bot.command(aliases=["fightercard", "profile"])
async def fighter(ctx, *, fighter_name: str = None):
    if not fighter_name:
        await ctx.send(
            "❌ Use:\n"
            "`!fighter Fighter Name`\n"
            "or `!fightercard Fighter Name`"
        )
        return

    fighter_key = fighter_name.casefold().strip()

    row = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE fighter_key = $1
        """,
        fighter_key,
    )

    if not row:
        await ctx.send(f"❌ **{fighter_name}** is not registered in OSBL.")
        return

    # Refresh the fighter's division rank before presenting the official card.
    await update_division_rankings(row["division"])
    row = await bot.db.fetchrow(
        "SELECT * FROM fighters WHERE fighter_key = $1",
        fighter_key,
    )

    gym_name = _profile_gym_name(row["gym"])
    if row["champion"]:
        status = "👑 CHAMPION"
        division_rank = "CHAMPION"
    else:
        status = "🥊 ACTIVE FIGHTER"
        division_rank = f"#{row['division_rank']}" if row["division_rank"] else "Unranked"

    total_bouts = int(row["wins"] or 0) + int(row["losses"] or 0)
    win_rate = (int(row["wins"] or 0) / total_bouts * 100) if total_bouts else 0.0
    recent_results = await _recent_fighter_results(fighter_key, limit=3)

    embed = discord.Embed(
        title="🥊 OSBL OFFICIAL FIGHTER PROFILE",
        description=(
            f"## {row['fighter_name']}\n"
            f"**{row['division']} Division** • {status}\n"
            f"{_profile_title_eligibility(row)}"
        ),
        color=discord.Color.gold(),
    )

    embed.add_field(name="🏢 Gym", value=gym_name, inline=True)
    embed.add_field(name="🥊 Record", value=f"**{row['wins']}-{row['losses']}**", inline=True)
    embed.add_field(name="🏅 Division Rank", value=f"**{division_rank}**", inline=True)

    embed.add_field(name="💎 Ranking Points", value=f"**{row['rp']} RP**", inline=True)
    embed.add_field(name="📈 Progression", value=f"**{row['progression_rank']}**", inline=True)
    embed.add_field(name="📊 Win Rate", value=f"**{win_rate:.0f}%**", inline=True)

    embed.add_field(name="💰 Career Earnings", value=f"**${row['career_earnings']:,}**", inline=True)
    embed.add_field(name="🛡️ Title Defenses", value=f"**{row['title_defenses']}**", inline=True)
    embed.add_field(name="🎯 Title Status", value=_profile_title_eligibility(row), inline=True)

    embed.add_field(
        name="🕘 Recent Results",
        value="\n".join(recent_results) if recent_results else "No official fights recorded yet.",
        inline=False,
    )

    embed.set_footer(
        text=f"{FIGHTER_PROFILE_VERSION} • ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )

    try:
        card_bytes = _render_osbl_fighter_profile_card(row, recent_results)
        card_file = discord.File(card_bytes, filename="osbl_official_fighter_card.png")
        embed.set_image(url="attachment://osbl_official_fighter_card.png")
        await ctx.send(embed=embed, file=card_file)
    except Exception as exc:
        # Never block staff from seeing the fighter profile if artwork fails.
        embed.add_field(
            name="⚠️ Fighter Card Renderer",
            value=f"Profile data loaded, but the visual card could not render: `{exc}`",
            inline=False,
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
# OSBL OFFICIAL GYM SYSTEM
# COMMANDS:
# !gym <Gym Name>
# !gymroster <Gym Name>
# !gymstats <Gym Name>
# !gymstandings
# =========================================================

OFFICIAL_GYMS = {
    "RADEEMERS": {
        "promoter": "Dub Radeem",
        "aliases": ("radeemers", "radeemers gym", "radeemer gym", "radeem team", "the radeem team"),
    },
    "ROYAL HITTAZ": {
        "promoter": "Stormi North",
        "aliases": ("royal hittaz", "royal hittaz gym", "royal hittaz fight gym", "royal hitttaz"),
    },
    "FINESSE TOWN FIGHTERS": {
        "promoter": "Cheeda Finessa",
        "aliases": ("finesse town fighters", "finesse town fighters gym", "finesse town gym", "finesse town"),
    },
    "GROVE STREET GOATS": {
        "promoter": "Mr. Souls",
        "aliases": ("grove street goats", "grove street goats gym", "grove street goatz", "grove street"),
    },
}


def _norm_gym_text(value):
    return " ".join(str(value or "").casefold().strip().split())


def _resolve_official_gym(value):
    key = _norm_gym_text(value)
    if not key:
        return None
    for official, meta in OFFICIAL_GYMS.items():
        if key == _norm_gym_text(official):
            return official
        if key in {_norm_gym_text(x) for x in meta["aliases"]}:
            return official
    return None


async def _normalize_existing_gym_data():
    """
    Canonicalize existing official gym names without touching test gyms,
    independent fighters, or unrelated promotions.
    """
    fighter_rows = await bot.db.fetch(
        "SELECT fighter_key, fighter_name, gym FROM fighters"
    )

    fighter_updates = []
    for row in fighter_rows:
        official = _resolve_official_gym(row["gym"])
        current = str(row["gym"] or "").strip()
        if official and current != official:
            fighter_updates.append((official, row["fighter_key"]))

    if fighter_updates:
        await bot.db.executemany(
            """
            UPDATE fighters
            SET gym = $1,
                updated_at = NOW()
            WHERE fighter_key = $2
            """,
            fighter_updates,
        )

    ledger_rows = await bot.db.fetch(
        "SELECT id, gym_name FROM payout_ledger"
    )
    ledger_updates = []
    for row in ledger_rows:
        official = _resolve_official_gym(row["gym_name"])
        current = str(row["gym_name"] or "").strip()
        if official and current != official:
            ledger_updates.append((official, row["id"]))

    if ledger_updates:
        await bot.db.executemany(
            """
            UPDATE payout_ledger
            SET gym_name = $1
            WHERE id = $2
            """,
            ledger_updates,
        )

    return {
        "fighters_updated": len(fighter_updates),
        "ledger_rows_updated": len(ledger_updates),
    }


async def _gym_promoter(official_gym):
    try:
        promoter = await bot.db.fetchval(
            "SELECT promoter_name FROM gym_settings WHERE official_name = $1",
            official_gym,
        )
        if promoter:
            return promoter
    except Exception:
        pass
    return OFFICIAL_GYMS[official_gym]["promoter"]


def _fighter_belongs_to_gym(raw_gym, official_gym):
    raw = _norm_gym_text(raw_gym)
    if not raw:
        return False
    meta = OFFICIAL_GYMS[official_gym]
    accepted = {_norm_gym_text(official_gym)} | {_norm_gym_text(x) for x in meta["aliases"]}
    # Allow common stored variants like "RADEEMERS Gym" while avoiding unrelated partial matches.
    return raw in accepted


async def _gym_fighters(official_gym):
    rows = await bot.db.fetch("SELECT * FROM fighters ORDER BY division, rp DESC, wins DESC, fighter_name ASC")
    return [row for row in rows if _fighter_belongs_to_gym(row["gym"], official_gym)]


async def _gym_snapshot(official_gym):
    fighters = await _gym_fighters(official_gym)
    fighter_keys = {row["fighter_key"] for row in fighters}

    wins = sum(int(row["wins"] or 0) for row in fighters)
    losses = sum(int(row["losses"] or 0) for row in fighters)
    total_rp = sum(int(row["rp"] or 0) for row in fighters)
    earnings = sum(int(row["career_earnings"] or 0) for row in fighters)
    champions = [row for row in fighters if row["champion"]]
    title_defenses = sum(int(row["title_defenses"] or 0) for row in fighters)

    top_contenders = sum(1 for row in fighters if str(row["progression_rank"] or "").casefold() == "top contender")
    number_one_contenders = sum(1 for row in fighters if str(row["progression_rank"] or "").casefold() == "#1 contender")

    regular_wins = 0
    regular_sweeps = 0
    championship_wins = 0
    championship_sweeps = 0

    if fighter_keys:
        history = await bot.db.fetch(
            """
            SELECT fight_type, winner_key, score
            FROM fight_history
            WHERE undone = FALSE
            ORDER BY id ASC
            """
        )
        for fight in history:
            if fight["winner_key"] not in fighter_keys:
                continue
            fight_type = str(fight["fight_type"] or "").casefold()
            score = str(fight["score"] or "").strip()
            if "champ" in fight_type:
                championship_wins += 1
                if score == "3-0":
                    championship_sweeps += 1
            else:
                regular_wins += 1
                if score == "2-0":
                    regular_sweeps += 1

    point_parts = {
        "Regular Wins": regular_wins * 3,
        "2-0 Sweeps": regular_sweeps * 1,
        "Championship Wins": championship_wins * 10,
        "3-0 Championship Sweeps": championship_sweeps * 3,
        "Title Defenses": title_defenses * 6,
        "Top Contenders": top_contenders * 3,
        "#1 Contenders": number_one_contenders * 5,
        "Active Champions": len(champions) * 8,
    }
    gym_points = sum(point_parts.values())

    return {
        "official_name": official_gym,
        "promoter": await _gym_promoter(official_gym),
        "fighters": fighters,
        "roster_size": len(fighters),
        "wins": wins,
        "losses": losses,
        "total_rp": total_rp,
        "earnings": earnings,
        "champions": champions,
        "title_defenses": title_defenses,
        "top_contenders": top_contenders,
        "number_one_contenders": number_one_contenders,
        "regular_wins": regular_wins,
        "regular_sweeps": regular_sweeps,
        "championship_wins": championship_wins,
        "championship_sweeps": championship_sweeps,
        "point_parts": point_parts,
        "gym_points": gym_points,
    }


async def _all_gym_snapshots():
    snapshots = []
    for gym_name in OFFICIAL_GYMS:
        snapshots.append(await _gym_snapshot(gym_name))
    snapshots.sort(
        key=lambda g: (
            -g["gym_points"],
            -len(g["champions"]),
            -g["total_rp"],
            -g["wins"],
            g["official_name"],
        )
    )
    for index, snapshot in enumerate(snapshots, start=1):
        snapshot["gym_rank"] = index
    return snapshots


def _gym_usage():
    return (
        "❌ Use one of the official gym names:\n"
        "**RADEEMERS**\n"
        "**ROYAL HITTAZ**\n"
        "**FINESSE TOWN FIGHTERS**\n"
        "**GROVE STREET GOATS**"
    )


GYM_POSTER_TEMPLATES = {
    "ROYAL HITTAZ": "osbl_gym_royal_hittaz.png",
    "RADEEMERS": "osbl_gym_radeemers.png",
    "FINESSE TOWN FIGHTERS": "osbl_gym_finesse_town.png",
    "GROVE STREET GOATS": "osbl_gym_grove_street_goats.png",
}


def _render_gym_poster(snapshot):
    from PIL import Image, ImageDraw

    official = snapshot["official_name"]
    filename = GYM_POSTER_TEMPLATES[official]
    path = Path(__file__).resolve().parent / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing gym poster template: {filename}")

    image = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    w, h = image.size

    # Live-stat strip: intentionally covers the template's baked-in stat row so
    # the Discord poster always reflects current OSBL database values.
    top = int(h * 0.735)
    bottom = int(h * 0.925)
    margin = int(w * 0.055)
    draw.rounded_rectangle(
        (margin, top, w - margin, bottom),
        radius=max(14, int(h * 0.018)),
        fill=(5, 5, 5, 235),
        outline=(212, 170, 55, 255),
        width=max(2, int(w * 0.0025)),
    )

    title_font = _fit_font(draw, "LIVE OSBL GYM STATS", w * 0.38, int(h * 0.036), 18)
    _draw_centered(
        draw,
        (margin, top + 4, w - margin, top + int(h * 0.055)),
        "LIVE OSBL GYM STATS",
        title_font,
        fill=(238, 198, 76),
        stroke=1,
    )

    stats = [
        ("GYM RANK", f"#{snapshot['gym_rank']}"),
        ("GYM POINTS", str(snapshot['gym_points'])),
        ("RECORD", f"{snapshot['wins']}-{snapshot['losses']}"),
        ("TOTAL RP", str(snapshot['total_rp'])),
        ("FIGHTERS", str(snapshot['roster_size'])),
        ("CHAMPIONS", str(len(snapshot['champions']))),
        ("DEFENSES", str(snapshot['title_defenses'])),
        ("EARNINGS", f"${snapshot['earnings']:,}"),
    ]

    row_top = top + int(h * 0.062)
    row_bottom = bottom - int(h * 0.032)
    usable_w = (w - 2 * margin)
    cell_w = usable_w / len(stats)
    label_font = _load_osbl_font(max(13, int(h * 0.018)), bold=True)
    value_font = _load_osbl_font(max(18, int(h * 0.029)), bold=True)

    for i, (label, value) in enumerate(stats):
        x1 = margin + int(i * cell_w)
        x2 = margin + int((i + 1) * cell_w)
        if i:
            draw.line((x1, row_top, x1, row_bottom), fill=(212, 170, 55, 190), width=2)
        lf = _fit_font(draw, label, cell_w - 12, int(h * 0.018), 10)
        vf = _fit_font(draw, value, cell_w - 10, int(h * 0.03), 13)
        _draw_centered(draw, (x1 + 4, row_top, x2 - 4, row_top + int(h * 0.032)), label, lf, fill=(230, 220, 190), stroke=1)
        _draw_centered(draw, (x1 + 4, row_top + int(h * 0.031), x2 - 4, row_bottom), value, vf, fill=(255, 255, 255), stroke=2)

    footer_font = _fit_font(draw, GYM_POSTER_VERSION, w * 0.28, int(h * 0.014), 9)
    draw.text(
        (margin + 8, bottom - int(h * 0.026)),
        GYM_POSTER_VERSION,
        font=footer_font,
        fill=(220, 205, 160),
        stroke_width=1,
        stroke_fill=(0, 0, 0),
    )

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


async def _send_gym_poster(ctx, official):
    snapshots = await _all_gym_snapshots()
    g = next(x for x in snapshots if x["official_name"] == official)
    poster = _render_gym_poster(g)
    attachment_name = "osbl_" + official.casefold().replace(" ", "_") + "_gym_poster.png"
    file = discord.File(poster, filename=attachment_name)
    embed = discord.Embed(
        title=f"🏢 OSBL OFFICIAL GYM BANNER — {official}",
        description=(
            f"**Gym Rank #{g['gym_rank']} • {g['gym_points']} GP**\n"
            f"Leader / Promoter: **{g['promoter']}** • Record **{g['wins']}-{g['losses']}** • **{g['total_rp']} RP**"
        ),
        color=discord.Color.gold(),
    )
    embed.set_image(url=f"attachment://{attachment_name}")
    embed.set_footer(text=f"{GYM_POSTER_VERSION} • LIVE OSBL GYM DATA")
    await ctx.send(embed=embed, file=file)


# =========================================================
# OSBL GYM MANAGEMENT
# WRITE COMMANDS ARE STAFF-ONLY
# =========================================================


def _fighter_key_from_name(name):
    return str(name or "").casefold().strip()


async def _log_gym_management(ctx, action, fighter_row=None, old_value=None, new_value=None):
    await bot.db.execute(
        """
        INSERT INTO gym_management_log (
            action, fighter_key, fighter_name, old_value, new_value,
            staff_id, staff_name
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        action,
        fighter_row["fighter_key"] if fighter_row else None,
        fighter_row["fighter_name"] if fighter_row else None,
        old_value,
        new_value,
        ctx.author.id,
        ctx.author.display_name,
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def setgym(ctx, *, details: str = None):
    if not details or "|" not in details:
        await ctx.send(
            "❌ **GYM ASSIGNMENT FORMAT**\n"
            "`!setgym Fighter Name | Gym Name`\n"
            "Example: `!setgym Hello Kitty | Royal HITTAZ`"
        )
        return

    fighter_name, gym_name = [part.strip() for part in details.split("|", 1)]
    official = _resolve_official_gym(gym_name)
    if not official:
        await ctx.send(_gym_usage())
        return

    fighter_key = _fighter_key_from_name(fighter_name)
    row = await bot.db.fetchrow(
        "SELECT * FROM fighters WHERE fighter_key = $1",
        fighter_key,
    )
    if not row:
        await ctx.send(f"❌ **{fighter_name}** is not registered in OSBL.")
        return

    old_gym = str(row["gym"] or "").strip() or "Independent / No Gym Assigned"
    if _fighter_belongs_to_gym(row["gym"], official):
        await ctx.send(f"ℹ️ **{row['fighter_name']}** is already assigned to **{official}**.")
        return

    await bot.db.execute(
        "UPDATE fighters SET gym = $1, updated_at = NOW() WHERE fighter_key = $2",
        official,
        fighter_key,
    )
    await _log_gym_management(ctx, "SET_GYM", row, old_gym, official)

    embed = discord.Embed(
        title="✅ OSBL GYM ASSIGNMENT UPDATED",
        description=f"**{row['fighter_name']}** is now assigned to **{official}**.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Previous Gym", value=old_gym, inline=True)
    embed.add_field(name="New Gym", value=official, inline=True)
    embed.add_field(name="Leader / Promoter", value=await _gym_promoter(official), inline=False)
    embed.set_footer(text=f"{GYM_MANAGEMENT_VERSION} • Updated by {ctx.author.display_name}")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def removegym(ctx, *, fighter_name: str = None):
    if not fighter_name:
        await ctx.send("❌ Use: `!removegym Fighter Name`")
        return

    fighter_key = _fighter_key_from_name(fighter_name)
    row = await bot.db.fetchrow(
        "SELECT * FROM fighters WHERE fighter_key = $1",
        fighter_key,
    )
    if not row:
        await ctx.send(f"❌ **{fighter_name}** is not registered in OSBL.")
        return

    old_gym = str(row["gym"] or "").strip()
    if not old_gym:
        await ctx.send(f"ℹ️ **{row['fighter_name']}** already has no gym assigned.")
        return

    await bot.db.execute(
        "UPDATE fighters SET gym = '', updated_at = NOW() WHERE fighter_key = $1",
        fighter_key,
    )
    await _log_gym_management(ctx, "REMOVE_GYM", row, old_gym, "Independent / No Gym Assigned")

    embed = discord.Embed(
        title="✅ OSBL GYM ASSIGNMENT REMOVED",
        description=f"**{row['fighter_name']}** is now **Independent / No Gym Assigned**.",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Previous Gym", value=old_gym, inline=False)
    embed.set_footer(text=f"{GYM_MANAGEMENT_VERSION} • Updated by {ctx.author.display_name}")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def setpromoter(ctx, *, details: str = None):
    if not details or "|" not in details:
        await ctx.send(
            "❌ **PROMOTER UPDATE FORMAT**\n"
            "`!setpromoter Gym Name | Promoter Name`\n"
            "Example: `!setpromoter Royal HITTAZ | Stormi North`"
        )
        return

    gym_name, promoter_name = [part.strip() for part in details.split("|", 1)]
    official = _resolve_official_gym(gym_name)
    if not official:
        await ctx.send(_gym_usage())
        return
    if not promoter_name:
        await ctx.send("❌ Promoter name cannot be blank.")
        return

    old_promoter = await _gym_promoter(official)
    await bot.db.execute(
        """
        INSERT INTO gym_settings (
            official_name, promoter_name, updated_by_id, updated_by_name, updated_at
        )
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (official_name)
        DO UPDATE SET
            promoter_name = EXCLUDED.promoter_name,
            updated_by_id = EXCLUDED.updated_by_id,
            updated_by_name = EXCLUDED.updated_by_name,
            updated_at = NOW()
        """,
        official,
        promoter_name,
        ctx.author.id,
        ctx.author.display_name,
    )
    await _log_gym_management(ctx, "SET_PROMOTER", None, f"{official}: {old_promoter}", f"{official}: {promoter_name}")

    embed = discord.Embed(
        title="🎙️ OSBL GYM PROMOTER UPDATED",
        description=f"**{official}** now lists **{promoter_name}** as Leader / Promoter.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Previous", value=old_promoter, inline=True)
    embed.add_field(name="Current", value=promoter_name, inline=True)
    embed.set_footer(text=f"{GYM_MANAGEMENT_VERSION} • Updated by {ctx.author.display_name}")
    await ctx.send(embed=embed)


@bot.command()
async def gymcheck(ctx, *, fighter_name: str = None):
    if not fighter_name:
        await ctx.send("❌ Use: `!gymcheck Fighter Name`")
        return

    fighter_key = _fighter_key_from_name(fighter_name)
    row = await bot.db.fetchrow(
        "SELECT * FROM fighters WHERE fighter_key = $1",
        fighter_key,
    )
    if not row:
        await ctx.send(f"❌ **{fighter_name}** is not registered in OSBL.")
        return

    official = _resolve_official_gym(row["gym"])
    if official:
        promoter = await _gym_promoter(official)
        snapshots = await _all_gym_snapshots()
        g = next(x for x in snapshots if x["official_name"] == official)
        description = (
            f"**{row['fighter_name']}** is assigned to **{official}**.\n"
            f"Leader / Promoter: **{promoter}**\n"
            f"Current Gym Rank: **#{g['gym_rank']}** • **{g['gym_points']} GP**"
        )
    else:
        description = (
            f"**{row['fighter_name']}** is currently **Independent / No Gym Assigned**."
        )

    embed = discord.Embed(
        title="🏢 OSBL FIGHTER GYM CHECK",
        description=description,
        color=discord.Color.gold(),
    )
    embed.add_field(name="Division", value=row["division"], inline=True)
    embed.add_field(name="Record", value=f"{row['wins']}-{row['losses']}", inline=True)
    embed.add_field(name="RP", value=f"{row['rp']} RP", inline=True)
    embed.set_footer(text=GYM_MANAGEMENT_VERSION)
    await ctx.send(embed=embed)


@bot.command()
async def gymposter(ctx, *, gym_name: str = None):
    official = _resolve_official_gym(gym_name)
    if not official:
        await ctx.send(
            _gym_usage()
            + "\n\nPoster format: `!gymposter Royal HITTAZ`"
        )
        return
    try:
        await _send_gym_poster(ctx, official)
    except Exception as exc:
        await ctx.send(f"❌ Gym poster could not render: `{exc}`")


@bot.command()
async def gymposterall(ctx):
    await ctx.send("🏢 **OSBL OFFICIAL GYM BANNERS** — refreshing live gym data...")
    for official in OFFICIAL_GYMS:
        try:
            await _send_gym_poster(ctx, official)
        except Exception as exc:
            await ctx.send(f"❌ **{official}** poster could not render: `{exc}`")


@bot.command()
async def gymstandings(ctx):
    snapshots = await _all_gym_snapshots()
    lines = []
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for g in snapshots:
        badge = medals.get(g["gym_rank"], f"#{g['gym_rank']}")
        champ_text = f"{len(g['champions'])} Champ" + ("s" if len(g["champions"]) != 1 else "")
        lines.append(
            f"{badge} **{g['official_name']}** — **{g['gym_points']} GP**\n"
            f"🏆 {champ_text} • 🥊 {g['wins']}-{g['losses']} • 💎 {g['total_rp']} RP • 👥 {g['roster_size']} Fighters"
        )

    embed = discord.Embed(
        title="🏢 OSBL OFFICIAL GYM STANDINGS",
        description="\n\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="📊 Gym Points Formula",
        value=(
            "Regular Win **+3** • 2-0 Sweep **+1**\n"
            "Championship Win **+10** • 3-0 Champ Sweep **+3**\n"
            "Title Defense **+6** • Top Contender **+3**\n"
            "#1 Contender **+5** • Active Champion **+8**"
        ),
        inline=False,
    )
    embed.set_footer(text=f"{GYM_SYSTEM_VERSION} • ONE LEAGUE. ONE STANDARD. ONE CHAMPION.")
    await ctx.send(embed=embed)


@bot.command()
async def gym(ctx, *, gym_name: str = None):
    official = _resolve_official_gym(gym_name)
    if not official:
        await ctx.send(_gym_usage())
        return

    snapshots = await _all_gym_snapshots()
    g = next(x for x in snapshots if x["official_name"] == official)
    champion_names = ", ".join(row["fighter_name"] for row in g["champions"]) or "None"

    embed = discord.Embed(
        title=f"🏢 OSBL OFFICIAL GYM PROFILE — {official}",
        description=f"**Gym Rank #{g['gym_rank']}** • **{g['gym_points']} Gym Points**",
        color=discord.Color.gold(),
    )
    embed.add_field(name="🎙️ Leader / Promoter", value=g["promoter"], inline=True)
    embed.add_field(name="👥 Roster Size", value=str(g["roster_size"]), inline=True)
    embed.add_field(name="🥊 Combined Record", value=f"{g['wins']}-{g['losses']}", inline=True)
    embed.add_field(name="💎 Total Fighter RP", value=f"{g['total_rp']} RP", inline=True)
    embed.add_field(name="👑 Active Champions", value=str(len(g["champions"])), inline=True)
    embed.add_field(name="🛡️ Title Defenses", value=str(g["title_defenses"]), inline=True)
    embed.add_field(name="💰 Combined Career Earnings", value=f"${g['earnings']:,}", inline=False)
    embed.add_field(name="🏆 Champions", value=champion_names, inline=False)
    embed.set_footer(text=f"{GYM_SYSTEM_VERSION} • Use !gymroster {official} for the roster")
    await ctx.send(embed=embed)


@bot.command()
async def gymstats(ctx, *, gym_name: str = None):
    official = _resolve_official_gym(gym_name)
    if not official:
        await ctx.send(_gym_usage())
        return

    snapshots = await _all_gym_snapshots()
    g = next(x for x in snapshots if x["official_name"] == official)
    parts = g["point_parts"]
    embed = discord.Embed(
        title=f"📊 OSBL GYM STATS — {official}",
        description=f"**Gym Rank #{g['gym_rank']} • {g['gym_points']} GP**",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="🥊 Performance",
        value=(
            f"Regular Wins: **{g['regular_wins']}** → +{parts['Regular Wins']} GP\n"
            f"2-0 Sweeps: **{g['regular_sweeps']}** → +{parts['2-0 Sweeps']} GP\n"
            f"Championship Wins: **{g['championship_wins']}** → +{parts['Championship Wins']} GP\n"
            f"3-0 Champ Sweeps: **{g['championship_sweeps']}** → +{parts['3-0 Championship Sweeps']} GP"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏆 Championship / Ranking Bonuses",
        value=(
            f"Title Defenses: **{g['title_defenses']}** → +{parts['Title Defenses']} GP\n"
            f"Top Contenders: **{g['top_contenders']}** → +{parts['Top Contenders']} GP\n"
            f"#1 Contenders: **{g['number_one_contenders']}** → +{parts['#1 Contenders']} GP\n"
            f"Active Champions: **{len(g['champions'])}** → +{parts['Active Champions']} GP"
        ),
        inline=False,
    )
    embed.add_field(
        name="📈 Team Totals",
        value=f"Record **{g['wins']}-{g['losses']}** • Total RP **{g['total_rp']}** • Earnings **${g['earnings']:,}**",
        inline=False,
    )
    embed.set_footer(text=f"{GYM_SYSTEM_VERSION} • Gym Points update automatically from live OSBL data")
    await ctx.send(embed=embed)


@bot.command()
async def gymroster(ctx, *, gym_name: str = None):
    official = _resolve_official_gym(gym_name)
    if not official:
        await ctx.send(_gym_usage())
        return

    fighters = await _gym_fighters(official)
    promoter = await _gym_promoter(official)
    embed = discord.Embed(
        title=f"🥊 {official} — OFFICIAL GYM ROSTER",
        description=f"Leader / Promoter: **{promoter}**",
        color=discord.Color.gold(),
    )

    if not fighters:
        embed.add_field(name="Roster", value="No fighters currently assigned.", inline=False)
    else:
        division_order = ("Lightweight", "Middleweight", "Heavyweight")
        for division in division_order:
            division_rows = [row for row in fighters if row["division"] == division]
            if not division_rows:
                continue
            division_rows.sort(key=lambda r: (not bool(r["champion"]), -(int(r["rp"] or 0)), -(int(r["wins"] or 0)), r["fighter_name"]))
            lines = []
            for row in division_rows:
                if row["champion"]:
                    rank_text = "👑 Champion"
                else:
                    rank_text = f"#{row['division_rank']}" if row["division_rank"] else "Unranked"
                lines.append(
                    f"**{row['fighter_name']}** — {row['wins']}-{row['losses']} • {row['rp']} RP • {row['progression_rank']} • {rank_text}"
                )
            embed.add_field(name=f"🥊 {division}", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"{GYM_SYSTEM_VERSION} • {len(fighters)} fighters")
    await ctx.send(embed=embed)

# =========================================================
# OSBL GYM HISTORY / SEASON ARCHIVE
# COMMANDS:
# !archivegymseason <Season Name>   (Commissioner only)
# !gymseason <Season Name>
# !gymhistory <Gym Name>
# !gymseasonlist
# =========================================================


def _clean_season_name(value):
    return " ".join(str(value or "").strip().split())


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def archivegymseason(ctx, *, season_name: str = None):
    season_name = _clean_season_name(season_name)
    if not season_name:
        await ctx.send("❌ Use: `!archivegymseason Season Name`")
        return

    existing = await bot.db.fetchval(
        "SELECT COUNT(*) FROM gym_season_history WHERE LOWER(season_name) = LOWER($1)",
        season_name,
    )
    if existing:
        await ctx.send(
            f"❌ **{season_name}** already has an archived gym snapshot. "
            "OSBL season archives are locked to preserve history."
        )
        return

    snapshots = await _all_gym_snapshots()
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            for g in snapshots:
                await conn.execute(
                    """
                    INSERT INTO gym_season_history (
                        season_name, gym_name, promoter_name, gym_rank, gym_points,
                        wins, losses, total_rp, roster_size, champions,
                        title_defenses, earnings, archived_by_id, archived_by_name
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                    """,
                    season_name,
                    g["official_name"],
                    g["promoter"],
                    int(g["gym_rank"]),
                    int(g["gym_points"]),
                    int(g["wins"]),
                    int(g["losses"]),
                    int(g["total_rp"]),
                    int(g["roster_size"]),
                    len(g["champions"]),
                    int(g["title_defenses"]),
                    int(g["earnings"]),
                    ctx.author.id,
                    ctx.author.display_name,
                )

    winner = snapshots[0]
    embed = discord.Embed(
        title="🏆 OSBL GYM SEASON ARCHIVED",
        description=(
            f"**{season_name}** has been permanently archived.\n"
            f"🥇 Gym of the Season: **{winner['official_name']}** — **{winner['gym_points']} GP**"
        ),
        color=discord.Color.gold(),
    )
    for g in snapshots:
        embed.add_field(
            name=f"#{g['gym_rank']} {g['official_name']}",
            value=(
                f"**{g['gym_points']} GP** • Record **{g['wins']}-{g['losses']}** • "
                f"**{g['total_rp']} RP** • {g['roster_size']} Fighters"
            ),
            inline=False,
        )
    embed.set_footer(text=f"{GYM_HISTORY_VERSION} • Archived by {ctx.author.display_name}")
    await ctx.send(embed=embed)


@bot.command()
async def gymseason(ctx, *, season_name: str = None):
    season_name = _clean_season_name(season_name)
    if not season_name:
        await ctx.send("❌ Use: `!gymseason Season Name`")
        return

    rows = await bot.db.fetch(
        """
        SELECT *
        FROM gym_season_history
        WHERE LOWER(season_name) = LOWER($1)
        ORDER BY gym_rank ASC, gym_name ASC
        """,
        season_name,
    )
    if not rows:
        await ctx.send(f"❌ No archived gym season found for **{season_name}**.")
        return

    official_season = rows[0]["season_name"]
    winner = rows[0]
    embed = discord.Embed(
        title=f"📚 OSBL GYM SEASON — {official_season}",
        description=f"🏆 **Gym of the Season: {winner['gym_name']}** — {winner['gym_points']} GP",
        color=discord.Color.gold(),
    )
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for row in rows:
        badge = medals.get(int(row["gym_rank"]), f"#{row['gym_rank']}")
        embed.add_field(
            name=f"{badge} {row['gym_name']}",
            value=(
                f"Leader / Promoter: **{row['promoter_name']}**\n"
                f"**{row['gym_points']} GP** • Record **{row['wins']}-{row['losses']}** • "
                f"**{row['total_rp']} RP**\n"
                f"👥 {row['roster_size']} • 👑 {row['champions']} • 🛡️ {row['title_defenses']} • "
                f"💰 ${int(row['earnings']):,}"
            ),
            inline=False,
        )
    embed.set_footer(text=f"{GYM_HISTORY_VERSION} • Historical snapshot — read only")
    await ctx.send(embed=embed)


@bot.command()
async def gymhistory(ctx, *, gym_name: str = None):
    official = _resolve_official_gym(gym_name)
    if not official:
        await ctx.send(_gym_usage())
        return

    rows = await bot.db.fetch(
        """
        SELECT *
        FROM gym_season_history
        WHERE gym_name = $1
        ORDER BY archived_at DESC, id DESC
        LIMIT 10
        """,
        official,
    )
    if not rows:
        await ctx.send(f"📚 **{official}** has no archived OSBL gym seasons yet.")
        return

    wins = sum(1 for row in rows if int(row["gym_rank"]) == 1)
    embed = discord.Embed(
        title=f"📚 OSBL GYM HISTORY — {official}",
        description=f"Archived Seasons: **{len(rows)}** • Gym of the Season Wins: **{wins}**",
        color=discord.Color.gold(),
    )
    for row in rows:
        crown = " 🏆" if int(row["gym_rank"]) == 1 else ""
        embed.add_field(
            name=f"{row['season_name']} — #{row['gym_rank']}{crown}",
            value=(
                f"**{row['gym_points']} GP** • {row['wins']}-{row['losses']} • {row['total_rp']} RP • "
                f"{row['champions']} Champions • {row['title_defenses']} Defenses"
            ),
            inline=False,
        )
    embed.set_footer(text=f"{GYM_HISTORY_VERSION} • Showing up to 10 archived seasons")
    await ctx.send(embed=embed)


@bot.command()
async def gymseasonlist(ctx):
    rows = await bot.db.fetch(
        """
        SELECT season_name, MIN(archived_at) AS archived_at, COUNT(*) AS gym_count
        FROM gym_season_history
        GROUP BY season_name
        ORDER BY MIN(archived_at) DESC
        LIMIT 20
        """
    )
    if not rows:
        await ctx.send("📚 No gym seasons have been archived yet.")
        return

    lines = []
    for row in rows:
        dt = row["archived_at"]
        date_text = dt.strftime("%Y-%m-%d") if dt else "Unknown date"
        lines.append(f"• **{row['season_name']}** — {row['gym_count']} gyms • {date_text}")

    embed = discord.Embed(
        title="📚 OSBL GYM SEASON ARCHIVES",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{GYM_HISTORY_VERSION} • Use !gymseason <Season Name>")
    await ctx.send(embed=embed)



# =========================================================
# OSBL TEST CLEANUP SYSTEM
# Commissioner-only destructive cleanup with confirmation.
# Commands:
# !testfighterlist
# !deletetestfighter <Fighter Name>
# !confirmdeletetestfighter <Fighter Name>
# !deletetestseason <Season Name>
# !confirmdeletetestseason <Season Name>
# =========================================================

_PENDING_TEST_FIGHTER_DELETES = {}
_PENDING_TEST_SEASON_DELETES = {}


def _is_test_label(value):
    text = " ".join(str(value or "").casefold().replace("_", " ").split())
    return (
        "test" in text
        or "stand-in" in text
        or "stand in" in text
        or text.startswith("dummy ")
        or text == "dummy"
    )


@bot.command()
async def testfighterlist(ctx):
    rows = await bot.db.fetch(
        """
        SELECT fighter_name, division, gym, wins, losses, rp, champion
        FROM fighters
        ORDER BY fighter_name ASC
        """
    )
    rows = [row for row in rows if _is_test_label(row["fighter_name"])]
    if not rows:
        await ctx.send("🧹 No obvious test fighters are currently stored.")
        return

    lines = []
    for row in rows:
        champ = " 👑" if row["champion"] else ""
        lines.append(
            f"• **{row['fighter_name']}**{champ} — {row['division']} • "
            f"{row['wins']}-{row['losses']} • {row['rp']} RP • {row['gym']}"
        )
    embed = discord.Embed(
        title="🧪 OSBL TEST FIGHTERS",
        description="\n".join(lines),
        color=discord.Color.orange(),
    )
    embed.set_footer(text=f"{CLEANUP_SYSTEM_VERSION} • Read only")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def deletetestfighter(ctx, *, fighter_name: str = None):
    fighter_name = " ".join(str(fighter_name or "").strip().split())
    if not fighter_name:
        await ctx.send("❌ Use: `!deletetestfighter Fighter Name`")
        return

    key = fighter_name.casefold()
    row = await bot.db.fetchrow(
        "SELECT fighter_key, fighter_name, division, gym, wins, losses, rp FROM fighters WHERE fighter_key = $1",
        key,
    )
    if not row:
        await ctx.send(f"❌ Fighter **{fighter_name}** was not found.")
        return
    if not _is_test_label(row["fighter_name"]):
        await ctx.send(
            "🛑 **DELETE BLOCKED** — this command only deletes fighters whose names clearly identify them as test/stand-in data."
        )
        return

    history_count = await bot.db.fetchval(
        "SELECT COUNT(*) FROM fight_history WHERE winner_key = $1 OR loser_key = $1", key
    )
    booking_count = await bot.db.fetchval(
        "SELECT COUNT(*) FROM fight_bookings WHERE fighter1_key = $1 OR fighter2_key = $1", key
    )
    _PENDING_TEST_FIGHTER_DELETES[ctx.author.id] = key

    embed = discord.Embed(
        title="⚠️ CONFIRM TEST FIGHTER DELETE",
        description=(
            f"You are about to permanently remove **{row['fighter_name']}** and linked test data.\n\n"
            f"Division: **{row['division']}**\nGym: **{row['gym']}**\n"
            f"Record: **{row['wins']}-{row['losses']}** • **{row['rp']} RP**\n"
            f"Fight-history rows: **{history_count}**\nBookings: **{booking_count}**\n\n"
            f"Confirm with:\n`!confirmdeletetestfighter {row['fighter_name']}`"
        ),
        color=discord.Color.red(),
    )
    embed.set_footer(text=f"{CLEANUP_SYSTEM_VERSION} • Commissioner only")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def confirmdeletetestfighter(ctx, *, fighter_name: str = None):
    fighter_name = " ".join(str(fighter_name or "").strip().split())
    key = fighter_name.casefold()
    pending = _PENDING_TEST_FIGHTER_DELETES.get(ctx.author.id)
    if not pending or pending != key:
        await ctx.send("❌ No matching pending test-fighter deletion. Run `!deletetestfighter <name>` first.")
        return

    row = await bot.db.fetchrow("SELECT * FROM fighters WHERE fighter_key = $1", key)
    if not row or not _is_test_label(row["fighter_name"]):
        _PENDING_TEST_FIGHTER_DELETES.pop(ctx.author.id, None)
        await ctx.send("❌ Test fighter no longer exists or no longer qualifies for protected test cleanup.")
        return

    division = row["division"]
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            history_ids = await conn.fetch(
                "SELECT id FROM fight_history WHERE winner_key = $1 OR loser_key = $1", key
            )
            ids = [int(r["id"]) for r in history_ids]
            deleted_history = len(ids)
            if ids:
                await conn.execute("DELETE FROM payout_ledger WHERE fight_history_id = ANY($1::bigint[])", ids)
                await conn.execute("DELETE FROM undo_audit_log WHERE fight_history_id = ANY($1::bigint[])", ids)
                await conn.execute("DELETE FROM result_override_log WHERE duplicate_history_id = ANY($1::bigint[])", ids)
            await conn.execute("DELETE FROM undo_audit_log WHERE winner_key = $1 OR loser_key = $1", key)
            await conn.execute("DELETE FROM result_override_log WHERE winner_key = $1 OR loser_key = $1", key)
            bookings_status = await conn.execute(
                "DELETE FROM fight_bookings WHERE fighter1_key = $1 OR fighter2_key = $1", key
            )
            await conn.execute("DELETE FROM fight_history WHERE winner_key = $1 OR loser_key = $1", key)
            await conn.execute("DELETE FROM gym_management_log WHERE fighter_key = $1", key)
            await conn.execute("DELETE FROM fighters WHERE fighter_key = $1", key)
            await conn.execute(
                """
                INSERT INTO test_cleanup_audit_log
                    (cleanup_type, target_name, details, commissioner_id, commissioner_name)
                VALUES ('fighter', $1, $2, $3, $4)
                """,
                row["fighter_name"],
                f"Deleted fighter and {deleted_history} linked fight-history rows; bookings cleanup={bookings_status}",
                ctx.author.id,
                ctx.author.display_name,
            )

    _PENDING_TEST_FIGHTER_DELETES.pop(ctx.author.id, None)
    await update_division_rankings(division)
    await ctx.send(
        f"✅ **TEST FIGHTER CLEANED UP**\n**{row['fighter_name']}** and its linked test records have been permanently removed.\n"
        f"`{CLEANUP_SYSTEM_VERSION}`"
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def deletetestseason(ctx, *, season_name: str = None):
    season_name = _clean_season_name(season_name)
    if not season_name:
        await ctx.send("❌ Use: `!deletetestseason Season Name`")
        return
    if not _is_test_label(season_name):
        await ctx.send("🛑 **DELETE BLOCKED** — only season names clearly marked as TEST can be removed with this cleanup command.")
        return

    count = await bot.db.fetchval(
        "SELECT COUNT(*) FROM gym_season_history WHERE LOWER(season_name) = LOWER($1)", season_name
    )
    if not count:
        await ctx.send(f"❌ No archived gym season found for **{season_name}**.")
        return

    _PENDING_TEST_SEASON_DELETES[ctx.author.id] = season_name.casefold()
    await ctx.send(
        f"⚠️ **CONFIRM TEST SEASON DELETE**\nThis will permanently remove **{season_name}** ({count} gym snapshots).\n"
        f"Confirm with: `!confirmdeletetestseason {season_name}`"
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def confirmdeletetestseason(ctx, *, season_name: str = None):
    season_name = _clean_season_name(season_name)
    pending = _PENDING_TEST_SEASON_DELETES.get(ctx.author.id)
    if not pending or pending != season_name.casefold():
        await ctx.send("❌ No matching pending test-season deletion. Run `!deletetestseason <season>` first.")
        return
    if not _is_test_label(season_name):
        _PENDING_TEST_SEASON_DELETES.pop(ctx.author.id, None)
        await ctx.send("🛑 Delete blocked: only TEST season archives can be removed by this command.")
        return

    rows = await bot.db.fetch(
        "SELECT season_name FROM gym_season_history WHERE LOWER(season_name) = LOWER($1) LIMIT 1", season_name
    )
    if not rows:
        _PENDING_TEST_SEASON_DELETES.pop(ctx.author.id, None)
        await ctx.send(f"❌ No archived gym season found for **{season_name}**.")
        return
    official_name = rows[0]["season_name"]

    async with bot.db.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "DELETE FROM gym_season_history WHERE LOWER(season_name) = LOWER($1)", season_name
            )
            await conn.execute(
                """
                INSERT INTO test_cleanup_audit_log
                    (cleanup_type, target_name, details, commissioner_id, commissioner_name)
                VALUES ('gym_season', $1, $2, $3, $4)
                """,
                official_name,
                f"Deleted test gym-season archive; database result={result}",
                ctx.author.id,
                ctx.author.display_name,
            )

    _PENDING_TEST_SEASON_DELETES.pop(ctx.author.id, None)
    await ctx.send(
        f"✅ **TEST SEASON CLEANED UP**\nArchived gym season **{official_name}** has been permanently removed.\n"
        f"`{CLEANUP_SYSTEM_VERSION}`"
    )


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

           history_id = await conn.fetchval(
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
            RETURNING id
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

           await conn.executemany(
            """
            INSERT INTO payout_ledger (
                fight_history_id,
                fight_night_session_id,
                fighter_key,
                fighter_name,
                gym_name,
                division,
                fight_type,
                payout_role,
                progression_rank,
                amount
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (fight_history_id, fighter_key) DO NOTHING
            """,
            [
                (
                    history_id,
                    fight_night_session_id,
                    winner_key,
                    winner["fighter_name"],
                    winner["gym"],
                    winner["division"],
                    "regular",
                    "winner",
                    winner_progression,
                    winner_purse,
                ),
                (
                    history_id,
                    fight_night_session_id,
                    loser_key,
                    loser["fighter_name"],
                    loser["gym"],
                    loser["division"],
                    "regular",
                    "loser",
                    loser_progression,
                    loser_purse,
                ),
            ],
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
            history_id = await conn.fetchval(
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
                    RETURNING id
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

            await conn.executemany(
                """
                INSERT INTO payout_ledger (
                    fight_history_id,
                    fight_night_session_id,
                    fighter_key,
                    fighter_name,
                    gym_name,
                    division,
                    fight_type,
                    payout_role,
                    progression_rank,
                    amount
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (fight_history_id, fighter_key) DO NOTHING
                """,
                [
                    (
                        history_id,
                        fight_night_session_id,
                        winner_key,
                        winner["fighter_name"],
                        winner["gym"],
                        winner["division"],
                        "championship",
                        "winner",
                        winner_progression,
                        winner_purse,
                    ),
                    (
                        history_id,
                        fight_night_session_id,
                        loser_key,
                        loser["fighter_name"],
                        loser["gym"],
                        loser["division"],
                        "championship",
                        "loser",
                        loser_progression,
                        loser_purse,
                    ),
                ],
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
# OSBL AUTOMATIC DIVISION RANKINGS
# COMMANDS:
# !rankings Lightweight
# !rankings Middleweight
# !rankings Heavyweight
# !allrankings
# !top10 Lightweight
# !top10 Middleweight
# !top10 Heavyweight
# ============================================

DIVISION_NAME_MAP = {
    "lightweight": "Lightweight",
    "middleweight": "Middleweight",
    "heavyweight": "Heavyweight",
}


def _resolve_division_name(division):
    if not division:
        return None
    return DIVISION_NAME_MAP.get(str(division).lower().strip())


def _eligibility_line(rp):
    rp = int(rp or 0)
    if rp >= 140:
        return "🏆 **TITLE ELIGIBLE**"
    return f"🔒 **{140 - rp} RP** away from Title Eligibility"


async def _fetch_division_rankings(official_division, limit=None):
    # Always refresh official rankings immediately before display.
    await update_division_rankings(official_division)

    champion = await bot.db.fetchrow(
        """
        SELECT *
        FROM fighters
        WHERE division = $1
          AND champion = TRUE
        ORDER BY rp DESC, wins DESC, losses ASC, fighter_name ASC
        LIMIT 1
        """,
        official_division,
    )

    query = """
        SELECT *
        FROM fighters
        WHERE division = $1
          AND champion = FALSE
        ORDER BY division_rank ASC NULLS LAST, rp DESC, wins DESC, losses ASC, fighter_name ASC
    """
    args = [official_division]
    if limit is not None:
        query += " LIMIT $2"
        args.append(int(limit))

    fighters = await bot.db.fetch(query, *args)
    return champion, fighters


def _champion_text(champion):
    if not champion:
        return "**VACANT**"

    return (
        f"**{champion['fighter_name']}**\n"
        f"🏢 Gym: **{champion['gym']}**\n"
        f"🥊 Record: **{champion['wins']}-{champion['losses']}**\n"
        f"💎 RP: **{champion['rp']}**\n"
        f"📈 Progression: **{champion['progression_rank']}**\n"
        f"🛡️ Title Defenses: **{champion['title_defenses']}**"
    )


def _contender_entry(fighter, compact=False):
    rank = fighter['division_rank'] if fighter['division_rank'] is not None else "—"
    if compact:
        return (
            f"**#{rank} {fighter['fighter_name']}** — "
            f"{fighter['wins']}-{fighter['losses']} | **{fighter['rp']} RP** | "
            f"{fighter['progression_rank']}\n"
            f"🏢 {fighter['gym']} • {_eligibility_line(fighter['rp'])}"
        )

    return (
        f"**#{rank} — {fighter['fighter_name']}**\n"
        f"🏢 Gym: **{fighter['gym']}**\n"
        f"🥊 Record: **{fighter['wins']}-{fighter['losses']}**\n"
        f"💎 RP: **{fighter['rp']}**\n"
        f"📈 Progression: **{fighter['progression_rank']}**\n"
        f"{_eligibility_line(fighter['rp'])}"
    )


def _chunk_entries(entries, max_chars=950):
    chunks = []
    current = ""
    for entry in entries:
        piece = entry + "\n\n"
        if current and len(current) + len(piece) > max_chars:
            chunks.append(current.rstrip())
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current.rstrip())
    return chunks


async def _send_division_rankings(ctx, official_division, limit=None, compact=False, heading=None):
    champion, fighters = await _fetch_division_rankings(official_division, limit=limit)

    title_suffix = heading or "RANKINGS"
    embed = discord.Embed(
        title=f"🥊 OSBL {official_division.upper()} {title_suffix}",
        description=(
            "Official ONESTATE Boxing League standings • "
            "Rankings refresh automatically from current RP, record, and champion status."
        ),
        color=discord.Color.gold(),
    )

    embed.add_field(
        name="👑 CHAMPION",
        value=_champion_text(champion),
        inline=False,
    )

    entries = [_contender_entry(fighter, compact=compact) for fighter in fighters]
    chunks = _chunk_entries(entries)

    if chunks:
        for i, chunk in enumerate(chunks):
            label = "🥇 TOP 10" if limit == 10 and i == 0 else "🥇 CONTENDER RANKINGS"
            if i > 0:
                label = "🥊 CONTENDER RANKINGS CONT."
            embed.add_field(name=label, value=chunk, inline=False)
    else:
        embed.add_field(
            name="🥇 CONTENDER RANKINGS",
            value="No ranked contenders yet.",
            inline=False,
        )

    embed.set_footer(
        text=f"{RANKINGS_SYSTEM_VERSION} • 140+ RP = title eligible • Eligibility does not guarantee a title shot"
    )
    await ctx.send(embed=embed)


@bot.command()
async def rankings(ctx, *, division: str = None):
    official_division = _resolve_division_name(division)
    if not official_division:
        await ctx.send(
            "❌ Use:\n"
            "`!rankings Lightweight`\n"
            "`!rankings Middleweight`\n"
            "`!rankings Heavyweight`"
        )
        return

    await _send_division_rankings(ctx, official_division)


@bot.command()
async def top10(ctx, *, division: str = None):
    official_division = _resolve_division_name(division)
    if not official_division:
        await ctx.send(
            "❌ Use:\n"
            "`!top10 Lightweight`\n"
            "`!top10 Middleweight`\n"
            "`!top10 Heavyweight`"
        )
        return

    await _send_division_rankings(
        ctx,
        official_division,
        limit=10,
        compact=True,
        heading="TOP 10",
    )


@bot.command()
async def allrankings(ctx):
    await ctx.send("📊 **OSBL ALL-DIVISION RANKINGS — refreshing official standings...**")
    for official_division in ("Lightweight", "Middleweight", "Heavyweight"):
        await _send_division_rankings(ctx, official_division, compact=True)


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
                UPDATE payout_ledger
                SET status = 'reversed',
                    reversed_by_id = $2,
                    reversed_by_name = $3,
                    reversed_at = NOW()
                WHERE fight_history_id = $1
                  AND status = 'active'
                """,
                fight_id,
                ctx.author.id,
                ctx.author.display_name,
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
# OSBL PAYOUT / FINANCIAL TRACKING SYSTEM
# =========================================================

def _money(value):
    return f"${int(value or 0):,}"



async def _fighter_bank_snapshot(fighter_key):
    fighter = await bot.db.fetchrow(
        """
        SELECT fighter_key, fighter_name, division, gym, career_earnings
        FROM fighters
        WHERE fighter_key = $1
        """,
        fighter_key,
    )
    if not fighter:
        return None

    paid = await bot.db.fetchrow(
        """
        SELECT
            COUNT(*) AS paid_out_count,
            COALESCE(SUM(amount), 0) AS total_paid_out,
            MAX(created_at) AS last_paid_at
        FROM payout_cashouts
        WHERE fighter_key = $1
        """,
        fighter_key,
    )

    pending = await bot.db.fetchrow(
        """
        SELECT
            COUNT(*) AS pending_count,
            COALESCE(SUM(requested_amount), 0) AS pending_total
        FROM payout_requests
        WHERE fighter_key = $1
          AND status = 'pending'
        """,
        fighter_key,
    )

    career = int(fighter["career_earnings"] or 0)
    total_paid = int(paid["total_paid_out"] or 0)
    pending_total = int(pending["pending_total"] or 0)

    return {
        "fighter": fighter,
        "career_earnings": career,
        "total_paid_out": total_paid,
        "paid_out_count": int(paid["paid_out_count"] or 0),
        "pending_total": pending_total,
        "pending_count": int(pending["pending_count"] or 0),
        "available_balance": max(0, career - total_paid),
        "available_after_pending": max(0, career - total_paid - pending_total),
        "last_paid_at": paid["last_paid_at"],
    }


@bot.command()
async def fighterbank(ctx, *, fighter_name: str = None):
    """Show earned, paid, pending and available OSBL money for a fighter."""
    fighter_name = " ".join(str(fighter_name or "").strip().split())
    if not fighter_name:
        await ctx.send("❌ Use: `!fighterbank Fighter Name`")
        return

    bank = await _fighter_bank_snapshot(fighter_name.casefold())
    if not bank:
        await ctx.send(f"❌ Fighter **{fighter_name}** was not found.")
        return

    fighter = bank["fighter"]
    embed = discord.Embed(
        title=f"🏦 OSBL FIGHTER BANK — {fighter['fighter_name']}",
        description=f"{fighter['division']} • {fighter['gym']}",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="💰 Total Career Earnings",
        value=f"**{_money(bank['career_earnings'])}**",
        inline=True,
    )
    embed.add_field(
        name="✅ Actually Paid Out",
        value=f"**{_money(bank['total_paid_out'])}**",
        inline=True,
    )
    embed.add_field(
        name="🔢 Paid Out Count",
        value=f"**{bank['paid_out_count']}** completed cashout(s)",
        inline=True,
    )
    embed.add_field(
        name="🏦 Available / Stacked Balance",
        value=f"**{_money(bank['available_balance'])}**",
        inline=True,
    )
    embed.add_field(
        name="⏳ Pending Requests",
        value=(
            f"**{bank['pending_count']}** request(s)\n"
            f"**{_money(bank['pending_total'])}** pending"
        ),
        inline=True,
    )
    embed.add_field(
        name="💵 Available After Pending Requests",
        value=f"**{_money(bank['available_after_pending'])}**",
        inline=True,
    )
    embed.set_footer(
        text=f"{PAYOUT_SYSTEM_VERSION} • Earnings may stack until a payout is approved"
    )
    await ctx.send(embed=embed)


@bot.command()
async def payoutrequest(ctx, *, request_text: str = None):
    """
    Create a payout request.
    Usage:
      !payoutrequest Fighter Name | 100000
      !payoutrequest Fighter Name | all
    """
    request_text = str(request_text or "").strip()
    if "|" not in request_text:
        await ctx.send(
            "❌ Use: `!payoutrequest Fighter Name | Amount`\n"
            "Example: `!payoutrequest Killswitch | 50000`\n"
            "Or: `!payoutrequest Killswitch | all`"
        )
        return

    fighter_name, amount_text = [part.strip() for part in request_text.split("|", 1)]
    fighter_key = fighter_name.casefold()
    bank = await _fighter_bank_snapshot(fighter_key)
    if not bank:
        await ctx.send(f"❌ Fighter **{fighter_name}** was not found.")
        return

    amount_text_clean = amount_text.replace("$", "").replace(",", "").strip().casefold()
    if amount_text_clean in {"all", "max", "full"}:
        amount = bank["available_after_pending"]
    else:
        try:
            amount = int(amount_text_clean)
        except ValueError:
            await ctx.send("❌ Amount must be a whole-dollar number or `all`.")
            return

    if amount <= 0:
        await ctx.send("❌ Requested payout amount must be greater than $0.")
        return

    if amount > bank["available_after_pending"]:
        await ctx.send(
            "❌ **PAYOUT REQUEST EXCEEDS AVAILABLE BALANCE**\n"
            f"Available after pending requests: **{_money(bank['available_after_pending'])}**"
        )
        return

    request_id = await bot.db.fetchval(
        """
        INSERT INTO payout_requests (
            fighter_key,
            fighter_name,
            requested_amount,
            requested_by_id,
            requested_by_name
        )
        VALUES ($1,$2,$3,$4,$5)
        RETURNING id
        """,
        fighter_key,
        bank["fighter"]["fighter_name"],
        amount,
        ctx.author.id,
        ctx.author.display_name,
    )

    await ctx.send(
        "🧾 **OSBL PAYOUT REQUEST SUBMITTED**\n"
        f"Request ID: **#{request_id}**\n"
        f"Fighter: **{bank['fighter']['fighter_name']}**\n"
        f"Requested: **{_money(amount)}**\n"
        f"Stacked balance before request: **{_money(bank['available_balance'])}**\n"
        f"Status: **PENDING**\n"
        f"`{PAYOUT_SYSTEM_VERSION}`"
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def payoutrequests(ctx):
    rows = await bot.db.fetch(
        """
        SELECT id, fighter_name, requested_amount, requested_by_name, created_at
        FROM payout_requests
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT 25
        """
    )
    if not rows:
        await ctx.send(
            "✅ **OSBL PAYOUT REQUESTS**\nNo pending payout requests.\n"
            f"`{PAYOUT_SYSTEM_VERSION}`"
        )
        return

    lines = [
        f"**#{row['id']}** • **{row['fighter_name']}** • "
        f"{_money(row['requested_amount'])} • requested by {row['requested_by_name']}"
        for row in rows
    ]
    embed = discord.Embed(
        title="🧾 OSBL PENDING PAYOUT REQUESTS",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{PAYOUT_SYSTEM_VERSION} • Use !payfighter <Request ID>")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def payfighter(ctx, request_id: int = None):
    """Mark a pending payout request as actually paid."""
    if request_id is None:
        await ctx.send("❌ Use: `!payfighter <Request ID>`")
        return

    async with bot.db.acquire() as conn:
        async with conn.transaction():
            req = await conn.fetchrow(
                """
                SELECT *
                FROM payout_requests
                WHERE id = $1
                FOR UPDATE
                """,
                request_id,
            )
            if not req:
                await ctx.send(f"❌ Payout Request **#{request_id}** was not found.")
                return
            if req["status"] != "pending":
                await ctx.send(
                    f"❌ Payout Request **#{request_id}** is already "
                    f"**{str(req['status']).upper()}**."
                )
                return

            bank = await _fighter_bank_snapshot(req["fighter_key"])
            if not bank:
                await ctx.send("❌ Fighter no longer exists in the OSBL database.")
                return

            # Pending total includes this request. Compare against the actual
            # unpaid balance (career earnings - completed cashouts).
            if int(req["requested_amount"]) > bank["available_balance"]:
                await ctx.send(
                    "❌ **PAYOUT BLOCKED — INSUFFICIENT UNPAID EARNINGS**\n"
                    f"Current unpaid balance: **{_money(bank['available_balance'])}**"
                )
                return

            cashout_id = await conn.fetchval(
                """
                INSERT INTO payout_cashouts (
                    request_id,
                    fighter_key,
                    fighter_name,
                    amount,
                    paid_by_id,
                    paid_by_name
                )
                VALUES ($1,$2,$3,$4,$5,$6)
                RETURNING id
                """,
                req["id"],
                req["fighter_key"],
                req["fighter_name"],
                req["requested_amount"],
                ctx.author.id,
                ctx.author.display_name,
            )

            await conn.execute(
                """
                UPDATE payout_requests
                SET status = 'paid',
                    approved_by_id = $2,
                    approved_by_name = $3,
                    approved_at = NOW()
                WHERE id = $1
                """,
                request_id,
                ctx.author.id,
                ctx.author.display_name,
            )

    bank_after = await _fighter_bank_snapshot(req["fighter_key"])
    receipt_sent, receipt_detail = await _send_payout_receipt_to_fighter(cashout_id)

    if receipt_sent:
        receipt_status = f"📩 Receipt: **SENT to {receipt_detail}**"
    elif receipt_detail == "not_linked":
        receipt_status = (
            "⚠️ Receipt: **NOT SENT — no Discord account linked**\n"
            "Use `!linkfighterdiscord Fighter Name | @DiscordUser`."
        )
    elif receipt_detail == "dm_failed":
        receipt_status = (
            "⚠️ Receipt: **NOT DELIVERED — player DMs may be disabled**"
        )
    else:
        receipt_status = "⚠️ Receipt: **Could not be generated**"

    await ctx.send(
        "✅ **OSBL FIGHTER PAYOUT COMPLETED**\n"
        f"Cashout ID: **#{cashout_id}**\n"
        f"Request ID: **#{request_id}**\n"
        f"Fighter: **{req['fighter_name']}**\n"
        f"Actually Paid: **{_money(req['requested_amount'])}**\n"
        f"Total Paid Out: **{_money(bank_after['total_paid_out'])}**\n"
        f"Paid Out Count: **{bank_after['paid_out_count']}**\n"
        f"Remaining Stacked Balance: **{_money(bank_after['available_balance'])}**\n"
        f"{receipt_status}\n"
        f"`{PAYOUT_SYSTEM_VERSION}`"
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def rejectpayout(ctx, request_id: int = None):
    if request_id is None:
        await ctx.send("❌ Use: `!rejectpayout <Request ID>`")
        return

    result = await bot.db.execute(
        """
        UPDATE payout_requests
        SET status = 'rejected',
            rejected_by_id = $2,
            rejected_by_name = $3,
            rejected_at = NOW()
        WHERE id = $1
          AND status = 'pending'
        """,
        request_id,
        ctx.author.id,
        ctx.author.display_name,
    )

    if result.endswith("0"):
        await ctx.send(
            f"❌ Payout Request **#{request_id}** was not found or is not pending."
        )
        return

    await ctx.send(
        f"🚫 **PAYOUT REQUEST #{request_id} REJECTED**\n"
        f"Rejected by **{ctx.author.display_name}**\n"
        f"`{PAYOUT_SYSTEM_VERSION}`"
    )




async def _get_fighter_discord_link(fighter_key):
    return await bot.db.fetchrow(
        """
        SELECT fighter_key, fighter_name, discord_user_id, linked_by_name, linked_at
        FROM fighter_discord_links
        WHERE fighter_key = $1
        """,
        fighter_key,
    )


def _parse_discord_user_id(raw_value):
    value = str(raw_value or "").strip()
    match = re.fullmatch(r"<@!?(\d+)>", value)
    if match:
        return int(match.group(1))
    if value.isdigit():
        return int(value)
    return None


def _resolve_discord_user_id_from_context(ctx, raw_value):
    """
    Resolve a Discord account from:
      - @mention
      - numeric user ID
      - the command author's username/display/global name
      - a cached guild member username/display/global name
    """
    value = str(raw_value or "").strip()
    direct_id = _parse_discord_user_id(value)
    if direct_id is not None:
        return direct_id

    wanted = value.casefold()

    # This makes self-link testing from iPhone easy even without Member Intent.
    author_names = {
        str(getattr(ctx.author, "name", "") or "").casefold(),
        str(getattr(ctx.author, "display_name", "") or "").casefold(),
        str(getattr(ctx.author, "global_name", "") or "").casefold(),
    }
    if wanted and wanted in author_names:
        return int(ctx.author.id)

    guild = getattr(ctx, "guild", None)
    if guild is not None:
        for member in getattr(guild, "members", []):
            member_names = {
                str(getattr(member, "name", "") or "").casefold(),
                str(getattr(member, "display_name", "") or "").casefold(),
                str(getattr(member, "global_name", "") or "").casefold(),
            }
            if wanted and wanted in member_names:
                return int(member.id)

    return None


async def _build_payout_receipt_embed(cashout_id):
    cashout = await bot.db.fetchrow(
        """
        SELECT id, request_id, fighter_key, fighter_name, amount,
               paid_by_name, created_at
        FROM payout_cashouts
        WHERE id = $1
        """,
        cashout_id,
    )
    if not cashout:
        return None, None

    bank = await _fighter_bank_snapshot(cashout["fighter_key"])
    if not bank:
        return None, cashout

    fighter = bank["fighter"]
    embed = discord.Embed(
        title="🧾 OSBL OFFICIAL PAYOUT RECEIPT",
        description=(
            f"**{fighter['fighter_name']}**\n"
            f"{fighter['division']} • {fighter['gym']}"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="✅ Transaction Status",
        value="**PAID**",
        inline=True,
    )
    embed.add_field(
        name="💵 Amount Paid",
        value=f"**{_money(cashout['amount'])}**",
        inline=True,
    )
    embed.add_field(
        name="🧾 Transaction",
        value=(
            f"Cashout ID: **#{cashout['id']}**\n"
            f"Request ID: **#{cashout['request_id']}**"
            if cashout["request_id"] is not None
            else f"Cashout ID: **#{cashout['id']}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏦 Fighter Bank After Payment",
        value=(
            f"Career Earnings: **{_money(bank['career_earnings'])}**\n"
            f"Total Paid Out: **{_money(bank['total_paid_out'])}**\n"
            f"Paid Out Count: **{bank['paid_out_count']}**\n"
            f"Remaining Stacked Balance: **{_money(bank['available_balance'])}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="👤 Processed By",
        value=f"**{cashout['paid_by_name']}**",
        inline=False,
    )
    embed.set_footer(
        text=f"{PAYOUT_SYSTEM_VERSION} • ONE LEAGUE. ONE STANDARD. ONE CHAMPION."
    )
    return embed, cashout


async def _send_payout_receipt_to_fighter(cashout_id):
    embed, cashout = await _build_payout_receipt_embed(cashout_id)
    if not cashout:
        return False, "cashout_not_found"
    if embed is None:
        return False, "fighter_not_found"

    link = await _get_fighter_discord_link(cashout["fighter_key"])
    if not link:
        return False, "not_linked"

    user_id = int(link["discord_user_id"])
    try:
        user = bot.get_user(user_id)
        if user is None:
            user = await bot.fetch_user(user_id)
        await user.send(embed=embed)
        return True, user
    except (discord.Forbidden, discord.HTTPException):
        return False, "dm_failed"


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def linkfighterdiscord(ctx, *, link_text: str = None):
    """
    Link one OSBL fighter to one Discord account.
    Usage: !linkfighterdiscord Killswitch | @DiscordUser
    """
    link_text = str(link_text or "").strip()
    if "|" not in link_text:
        await ctx.send(
            "❌ Use: `!linkfighterdiscord Fighter Name | @DiscordUser`\n"
            "Example: `!linkfighterdiscord Killswitch | @PlayerName`"
        )
        return

    fighter_name, user_text = [part.strip() for part in link_text.split("|", 1)]
    fighter_key = fighter_name.casefold()
    fighter = await bot.db.fetchrow(
        """
        SELECT fighter_key, fighter_name, division, gym
        FROM fighters
        WHERE fighter_key = $1
        """,
        fighter_key,
    )
    if not fighter:
        await ctx.send(f"❌ Fighter **{fighter_name}** was not found.")
        return

    user_id = _resolve_discord_user_id_from_context(ctx, user_text)
    if user_id is None:
        await ctx.send(
            "❌ I couldn't resolve that Discord account.\n"
            "Use an actual Discord mention, a numeric Discord User ID, "
            "or the exact username/display name of a member currently visible to the bot."
        )
        return

    try:
        user = bot.get_user(user_id)
        if user is None:
            user = await bot.fetch_user(user_id)
    except discord.HTTPException:
        await ctx.send("❌ Discord user could not be resolved.")
        return

    existing_for_user = await bot.db.fetchrow(
        """
        SELECT fighter_name
        FROM fighter_discord_links
        WHERE discord_user_id = $1
          AND fighter_key <> $2
        """,
        user_id,
        fighter_key,
    )
    if existing_for_user:
        await ctx.send(
            "❌ **DISCORD ACCOUNT ALREADY LINKED**\n"
            f"That account is already linked to **{existing_for_user['fighter_name']}**."
        )
        return

    await bot.db.execute(
        """
        INSERT INTO fighter_discord_links (
            fighter_key,
            fighter_name,
            discord_user_id,
            linked_by_id,
            linked_by_name
        )
        VALUES ($1,$2,$3,$4,$5)
        ON CONFLICT (fighter_key)
        DO UPDATE SET
            fighter_name = EXCLUDED.fighter_name,
            discord_user_id = EXCLUDED.discord_user_id,
            linked_by_id = EXCLUDED.linked_by_id,
            linked_by_name = EXCLUDED.linked_by_name,
            updated_at = NOW()
        """,
        fighter_key,
        fighter["fighter_name"],
        user_id,
        ctx.author.id,
        ctx.author.display_name,
    )

    await ctx.send(
        "🔗 **OSBL FIGHTER DISCORD LINKED**\n"
        f"Fighter: **{fighter['fighter_name']}**\n"
        f"Discord: **{user}** (`{user_id}`)\n"
        "✅ Future successful cashouts will automatically send this player "
        "an official OSBL payout receipt by DM.\n"
        f"`{PAYOUT_SYSTEM_VERSION}`"
    )


@bot.command()
async def fighterdiscord(ctx, *, fighter_name: str = None):
    fighter_name = " ".join(str(fighter_name or "").strip().split())
    if not fighter_name:
        await ctx.send("❌ Use: `!fighterdiscord Fighter Name`")
        return

    fighter_key = fighter_name.casefold()
    fighter = await bot.db.fetchrow(
        "SELECT fighter_name FROM fighters WHERE fighter_key = $1",
        fighter_key,
    )
    if not fighter:
        await ctx.send(f"❌ Fighter **{fighter_name}** was not found.")
        return

    link = await _get_fighter_discord_link(fighter_key)
    if not link:
        await ctx.send(
            f"🔗 **{fighter['fighter_name']}** does not have a Discord account linked yet.\n"
            f"`{PAYOUT_SYSTEM_VERSION}`"
        )
        return

    await ctx.send(
        "🔗 **OSBL FIGHTER DISCORD LINK**\n"
        f"Fighter: **{fighter['fighter_name']}**\n"
        f"Discord User ID: `{link['discord_user_id']}`\n"
        f"Linked by: **{link['linked_by_name']}**\n"
        f"`{PAYOUT_SYSTEM_VERSION}`"
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def unlinkfighterdiscord(ctx, *, fighter_name: str = None):
    fighter_name = " ".join(str(fighter_name or "").strip().split())
    if not fighter_name:
        await ctx.send("❌ Use: `!unlinkfighterdiscord Fighter Name`")
        return

    fighter_key = fighter_name.casefold()
    result = await bot.db.execute(
        "DELETE FROM fighter_discord_links WHERE fighter_key = $1",
        fighter_key,
    )
    if result.endswith("0"):
        await ctx.send(f"❌ **{fighter_name}** does not currently have a Discord link.")
        return

    await ctx.send(
        "🔓 **OSBL FIGHTER DISCORD LINK REMOVED**\n"
        f"Fighter: **{fighter_name}**\n"
        f"`{PAYOUT_SYSTEM_VERSION}`"
    )


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def resendreceipt(ctx, cashout_id: int = None):
    if cashout_id is None:
        await ctx.send("❌ Use: `!resendreceipt <Cashout ID>`")
        return

    sent, detail = await _send_payout_receipt_to_fighter(cashout_id)
    if sent:
        await ctx.send(
            "📩 **OSBL PAYOUT RECEIPT RESENT**\n"
            f"Cashout ID: **#{cashout_id}**\n"
            f"Delivered to: **{detail}**\n"
            f"`{PAYOUT_SYSTEM_VERSION}`"
        )
        return

    if detail == "not_linked":
        await ctx.send(
            "⚠️ Receipt was not sent because this fighter has no Discord account linked.\n"
            "Use `!linkfighterdiscord Fighter Name | @DiscordUser` first."
        )
    elif detail == "dm_failed":
        await ctx.send(
            "⚠️ Receipt could not be delivered. The player's DMs may be disabled.\n"
            "The payout itself is unchanged."
        )
    else:
        await ctx.send(f"❌ Cashout **#{cashout_id}** could not be found or rebuilt.")



async def _osbl_treasury_snapshot():
    """Return league-wide fighter money totals based on live fighter earnings and cashout tables."""
    totals = await bot.db.fetchrow(
        """
        WITH cashouts AS (
            SELECT fighter_key, COALESCE(SUM(amount), 0) AS paid_total
            FROM payout_cashouts
            GROUP BY fighter_key
        ),
        pending AS (
            SELECT fighter_key, COALESCE(SUM(requested_amount), 0) AS pending_total
            FROM payout_requests
            WHERE status = 'pending'
            GROUP BY fighter_key
        )
        SELECT
            COUNT(*) AS fighter_count,
            COALESCE(SUM(f.career_earnings), 0) AS career_earnings,
            COALESCE(SUM(COALESCE(c.paid_total, 0)), 0) AS paid_out,
            COALESCE(SUM(
                GREATEST(f.career_earnings - COALESCE(c.paid_total, 0), 0)
            ), 0) AS stacked_balance,
            COALESCE(SUM(COALESCE(p.pending_total, 0)), 0) AS pending_total
        FROM fighters f
        LEFT JOIN cashouts c ON c.fighter_key = f.fighter_key
        LEFT JOIN pending p ON p.fighter_key = f.fighter_key
        """
    )

    pending_count = await bot.db.fetchval(
        """
        SELECT COUNT(*)
        FROM payout_requests
        WHERE status = 'pending'
        """
    )

    cashout_count = await bot.db.fetchval(
        """
        SELECT COUNT(*)
        FROM payout_cashouts
        """
    )

    return {
        "fighter_count": int(totals["fighter_count"] or 0),
        "career_earnings": int(totals["career_earnings"] or 0),
        "paid_out": int(totals["paid_out"] or 0),
        "stacked_balance": int(totals["stacked_balance"] or 0),
        "pending_total": int(totals["pending_total"] or 0),
        "pending_count": int(pending_count or 0),
        "cashout_count": int(cashout_count or 0),
    }


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def normalizegyms(ctx):
    """
    Re-run official gym-name normalization on demand.
    Safe: it only changes recognized aliases into canonical official names.
    """
    result = await _normalize_existing_gym_data()

    snapshots = await _all_gym_snapshots()
    lines = [
        f"**{g['official_name']}** — {g['roster_size']} fighter(s)"
        for g in snapshots
    ]

    embed = discord.Embed(
        title="🧹 OSBL GYM DATA NORMALIZED",
        description=(
            f"Fighter records corrected: **{result['fighters_updated']}**\n"
            f"Payout-ledger rows corrected: **{result['ledger_rows_updated']}**"
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="🏢 Canonical Official Gyms",
        value="\n".join(lines),
        inline=False,
    )
    embed.add_field(
        name="✅ Canonical Names",
        value=(
            "ROYAL HITTAZ\n"
            "RADEEMERS\n"
            "FINESSE TOWN FIGHTERS\n"
            "GROVE STREET GOATS"
        ),
        inline=False,
    )
    embed.set_footer(text=GYM_NORMALIZATION_VERSION)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def treasury(ctx):
    """League-wide OSBL financial dashboard."""
    snap = await _osbl_treasury_snapshot()

    gym_rows = await bot.db.fetch(
        """
        WITH cashouts AS (
            SELECT fighter_key, COALESCE(SUM(amount), 0) AS paid_total
            FROM payout_cashouts
            GROUP BY fighter_key
        ),
        pending AS (
            SELECT fighter_key, COALESCE(SUM(requested_amount), 0) AS pending_total
            FROM payout_requests
            WHERE status = 'pending'
            GROUP BY fighter_key
        )
        SELECT
            COALESCE(NULLIF(TRIM(f.gym), ''), 'Independent / No Gym Assigned') AS gym_name,
            COUNT(*) AS fighters,
            COALESCE(SUM(f.career_earnings), 0) AS earned,
            COALESCE(SUM(COALESCE(c.paid_total, 0)), 0) AS paid,
            COALESCE(SUM(
                GREATEST(f.career_earnings - COALESCE(c.paid_total, 0), 0)
            ), 0) AS owed,
            COALESCE(SUM(COALESCE(p.pending_total, 0)), 0) AS pending
        FROM fighters f
        LEFT JOIN cashouts c ON c.fighter_key = f.fighter_key
        LEFT JOIN pending p ON p.fighter_key = f.fighter_key
        GROUP BY 1
        ORDER BY owed DESC, earned DESC, gym_name ASC
        """
    )

    top_rows = await bot.db.fetch(
        """
        WITH cashouts AS (
            SELECT fighter_key, COALESCE(SUM(amount), 0) AS paid_total
            FROM payout_cashouts
            GROUP BY fighter_key
        )
        SELECT
            f.fighter_name,
            f.gym,
            f.career_earnings,
            COALESCE(c.paid_total, 0) AS paid,
            GREATEST(f.career_earnings - COALESCE(c.paid_total, 0), 0) AS owed
        FROM fighters f
        LEFT JOIN cashouts c ON c.fighter_key = f.fighter_key
        ORDER BY owed DESC, f.career_earnings DESC, f.fighter_name ASC
        LIMIT 5
        """
    )

    embed = discord.Embed(
        title="🏦 OSBL TREASURY DASHBOARD",
        description="Live financial exposure across the ONESTATE Boxing League.",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="💰 Total Fighter Career Earnings",
        value=f"**{_money(snap['career_earnings'])}**",
        inline=True,
    )
    embed.add_field(
        name="✅ Total Actually Paid",
        value=f"**{_money(snap['paid_out'])}**",
        inline=True,
    )
    embed.add_field(
        name="🏦 Total Stacked / Owed",
        value=f"**{_money(snap['stacked_balance'])}**",
        inline=True,
    )
    embed.add_field(
        name="⏳ Pending Payout Requests",
        value=(
            f"**{snap['pending_count']}** request(s)\n"
            f"**{_money(snap['pending_total'])}** pending"
        ),
        inline=True,
    )
    embed.add_field(
        name="🧾 Completed Cashouts",
        value=f"**{snap['cashout_count']}**",
        inline=True,
    )
    embed.add_field(
        name="🥊 Fighters Tracked",
        value=f"**{snap['fighter_count']}**",
        inline=True,
    )

    gym_lines = []
    for row in gym_rows:
        gym_lines.append(
            f"**{row['gym_name']}** — Owed {_money(row['owed'])} • "
            f"Paid {_money(row['paid'])} • Pending {_money(row['pending'])}"
        )
    embed.add_field(
        name="🏢 Financial Exposure by Gym",
        value="\n".join(gym_lines[:8]) if gym_lines else "No fighter financial data.",
        inline=False,
    )

    top_lines = []
    for i, row in enumerate(top_rows, start=1):
        top_lines.append(
            f"**#{i} {row['fighter_name']}** — {_money(row['owed'])} owed "
            f"({row['gym']})"
        )
    embed.add_field(
        name="📈 Largest Unpaid Fighter Balances",
        value="\n".join(top_lines) if top_lines else "No unpaid fighter balances.",
        inline=False,
    )

    embed.set_footer(
        text=f"{PAYOUT_SYSTEM_VERSION} • Live data • Career earnings minus completed cashouts"
    )
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def gymtreasury(ctx, *, gym_name: str = None):
    """Show the payout exposure for one official gym."""
    gym_name = " ".join(str(gym_name or "").strip().split())
    if not gym_name:
        await ctx.send("❌ Use: `!gymtreasury Gym Name`")
        return

    official = _resolve_official_gym(gym_name)
    if not official:
        await ctx.send(_gym_usage())
        return

    rows = await bot.db.fetch(
        """
        WITH cashouts AS (
            SELECT fighter_key, COALESCE(SUM(amount), 0) AS paid_total
            FROM payout_cashouts
            GROUP BY fighter_key
        ),
        pending AS (
            SELECT fighter_key, COALESCE(SUM(requested_amount), 0) AS pending_total
            FROM payout_requests
            WHERE status = 'pending'
            GROUP BY fighter_key
        )
        SELECT
            f.fighter_name,
            f.division,
            f.career_earnings,
            COALESCE(c.paid_total, 0) AS paid,
            GREATEST(f.career_earnings - COALESCE(c.paid_total, 0), 0) AS owed,
            COALESCE(p.pending_total, 0) AS pending
        FROM fighters f
        LEFT JOIN cashouts c ON c.fighter_key = f.fighter_key
        LEFT JOIN pending p ON p.fighter_key = f.fighter_key
        WHERE f.gym = $1
        ORDER BY owed DESC, f.career_earnings DESC, f.fighter_name ASC
        """,
        official,
    )

    if not rows:
        await ctx.send(f"❌ No fighters were found for gym **{official}**.")
        return

    earned = sum(int(row["career_earnings"] or 0) for row in rows)
    paid = sum(int(row["paid"] or 0) for row in rows)
    owed = sum(int(row["owed"] or 0) for row in rows)
    pending = sum(int(row["pending"] or 0) for row in rows)

    embed = discord.Embed(
        title=f"🏢 OSBL GYM TREASURY — {official}",
        description=f"Financial exposure for **{len(rows)} fighter(s)**.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="💰 Career Earnings", value=f"**{_money(earned)}**", inline=True)
    embed.add_field(name="✅ Paid Out", value=f"**{_money(paid)}**", inline=True)
    embed.add_field(name="🏦 Stacked / Owed", value=f"**{_money(owed)}**", inline=True)
    embed.add_field(name="⏳ Pending", value=f"**{_money(pending)}**", inline=True)

    fighter_lines = []
    for row in rows[:15]:
        fighter_lines.append(
            f"**{row['fighter_name']}** — Owed {_money(row['owed'])} • "
            f"Paid {_money(row['paid'])} • Pending {_money(row['pending'])}"
        )
    embed.add_field(
        name="🥊 Fighter Balances",
        value="\n".join(fighter_lines),
        inline=False,
    )
    embed.set_footer(text=PAYOUT_SYSTEM_VERSION)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def treasurytop(ctx, limit: int = 10):
    """Show the fighters with the largest unpaid balances."""
    limit = max(1, min(int(limit or 10), 20))

    rows = await bot.db.fetch(
        """
        WITH cashouts AS (
            SELECT fighter_key, COALESCE(SUM(amount), 0) AS paid_total
            FROM payout_cashouts
            GROUP BY fighter_key
        ),
        pending AS (
            SELECT fighter_key, COALESCE(SUM(requested_amount), 0) AS pending_total
            FROM payout_requests
            WHERE status = 'pending'
            GROUP BY fighter_key
        )
        SELECT
            f.fighter_name,
            f.division,
            f.gym,
            f.career_earnings,
            COALESCE(c.paid_total, 0) AS paid,
            GREATEST(f.career_earnings - COALESCE(c.paid_total, 0), 0) AS owed,
            COALESCE(p.pending_total, 0) AS pending
        FROM fighters f
        LEFT JOIN cashouts c ON c.fighter_key = f.fighter_key
        LEFT JOIN pending p ON p.fighter_key = f.fighter_key
        ORDER BY owed DESC, f.career_earnings DESC, f.fighter_name ASC
        LIMIT $1
        """,
        limit,
    )

    if not rows:
        await ctx.send("📈 No fighter treasury data is available.")
        return

    lines = []
    for i, row in enumerate(rows, start=1):
        lines.append(
            f"**#{i} {row['fighter_name']}** — Owed **{_money(row['owed'])}** • "
            f"Paid {_money(row['paid'])} • Pending {_money(row['pending'])} • {row['gym']}"
        )

    embed = discord.Embed(
        title="📈 OSBL TOP UNPAID BALANCES",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{PAYOUT_SYSTEM_VERSION} • Highest stacked balances")
    await ctx.send(embed=embed)


@bot.command()
async def payoutstatement(ctx, *, fighter_name: str = None):
    """
    Show a fighter's complete OSBL payout statement:
    earnings, paid-out total/count, stacked balance, pending requests,
    recent requests, completed cashouts, and rejected requests.
    """
    fighter_name = " ".join(str(fighter_name or "").strip().split())
    if not fighter_name:
        await ctx.send("❌ Use: `!payoutstatement Fighter Name`")
        return

    fighter_key = fighter_name.casefold()
    bank = await _fighter_bank_snapshot(fighter_key)
    if not bank:
        await ctx.send(f"❌ Fighter **{fighter_name}** was not found.")
        return

    fighter = bank["fighter"]

    request_rows = await bot.db.fetch(
        """
        SELECT id, requested_amount, status, requested_by_name,
               approved_by_name, rejected_by_name, created_at,
               approved_at, rejected_at
        FROM payout_requests
        WHERE fighter_key = $1
        ORDER BY id DESC
        LIMIT 10
        """,
        fighter_key,
    )

    cashout_rows = await bot.db.fetch(
        """
        SELECT id, request_id, amount, paid_by_name, note, created_at
        FROM payout_cashouts
        WHERE fighter_key = $1
        ORDER BY id DESC
        LIMIT 10
        """,
        fighter_key,
    )

    ledger_summary = await bot.db.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'active') AS active_entries,
            COALESCE(SUM(amount) FILTER (WHERE status = 'active'), 0) AS active_total,
            COUNT(*) FILTER (WHERE status = 'reversed') AS reversed_entries,
            COALESCE(SUM(amount) FILTER (WHERE status = 'reversed'), 0) AS reversed_total
        FROM payout_ledger
        WHERE fighter_key = $1
        """,
        fighter_key,
    )

    request_lines = []
    for row in request_rows:
        status = str(row["status"]).upper()
        extra = ""
        if status == "PAID" and row["approved_by_name"]:
            extra = f" • paid by {row['approved_by_name']}"
        elif status == "REJECTED" and row["rejected_by_name"]:
            extra = f" • rejected by {row['rejected_by_name']}"
        request_lines.append(
            f"**Req #{row['id']}** • {_money(row['requested_amount'])} • **{status}**{extra}"
        )

    cashout_lines = []
    for row in cashout_rows:
        cashout_lines.append(
            f"**Cashout #{row['id']}** • Req #{row['request_id'] or '—'} • "
            f"**{_money(row['amount'])}** • paid by {row['paid_by_name']}"
        )

    embed = discord.Embed(
        title=f"📄 OSBL PAYOUT STATEMENT — {fighter['fighter_name']}",
        description=f"{fighter['division']} • {fighter['gym']}",
        color=discord.Color.gold(),
    )

    embed.add_field(
        name="🏦 Account Summary",
        value=(
            f"Career Earnings: **{_money(bank['career_earnings'])}**\n"
            f"Actually Paid Out: **{_money(bank['total_paid_out'])}**\n"
            f"Paid Out Count: **{bank['paid_out_count']}**\n"
            f"Stacked Balance: **{_money(bank['available_balance'])}**\n"
            f"Pending Requests: **{bank['pending_count']}** "
            f"({_money(bank['pending_total'])})\n"
            f"Available After Pending: **{_money(bank['available_after_pending'])}**"
        ),
        inline=False,
    )

    embed.add_field(
        name="🥊 Fight Earnings Ledger",
        value=(
            f"Active Payout Entries: **{ledger_summary['active_entries']}**\n"
            f"Tracked Active Fight Payouts: **{_money(ledger_summary['active_total'])}**\n"
            f"Reversed Entries: **{ledger_summary['reversed_entries']}**\n"
            f"Reversed Value: **{_money(ledger_summary['reversed_total'])}**"
        ),
        inline=False,
    )

    embed.add_field(
        name="🧾 Recent Payout Requests",
        value="\n".join(request_lines) if request_lines else "No payout requests yet.",
        inline=False,
    )

    embed.add_field(
        name="✅ Recent Completed Cashouts",
        value="\n".join(cashout_lines) if cashout_lines else "No completed cashouts yet.",
        inline=False,
    )

    embed.set_footer(
        text=f"{PAYOUT_SYSTEM_VERSION} • Career earnings include pre-ledger OSBL earnings"
    )
    await ctx.send(embed=embed)


@bot.command()
async def payoutdesk(ctx):
    """Show the active OSBL purse schedule currently used by the result commands."""
    ladder = [
        ("Prospect", 50000, 25000),
        ("Rising Prospect", 75000, 35000),
        ("Contender", 100000, 50000),
        ("Top Contender", 150000, 75000),
        ("Elite Contender", 225000, 100000),
        ("#1 Contender", 350000, 150000),
    ]

    lines = []
    for rank, show, bonus in ladder:
        lines.append(
            f"**{rank}** — Show {_money(show)} • Win Bonus {_money(bonus)} • "
            f"Winner Total {_money(show + bonus)}"
        )

    embed = discord.Embed(
        title="💰 OSBL OFFICIAL PAYOUT DESK",
        description=(
            "Current purse schedule used automatically by OSBL fight-result commands.\n\n"
            + "\n".join(lines)
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="🏆 Championship",
        value=(
            f"Champion / Championship Winner: **{_money(5000000)}**\n"
            f"Championship Opponent: **{_money(500000)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="📒 Financial Tracking",
        value=(
            "Every new official result is written to the payout ledger automatically. "
            "If that result is undone, its ledger entries are marked **REVERSED**."
        ),
        inline=False,
    )
    embed.set_footer(text=f"{PAYOUT_SYSTEM_VERSION} • Live purse schedule")
    await ctx.send(embed=embed)


@bot.command()
async def fightpayout(ctx, history_id: int = None):
    if history_id is None:
        await ctx.send("❌ Use: `!fightpayout <History ID>`")
        return

    fight = await bot.db.fetchrow(
        """
        SELECT id, fight_type, winner_key, loser_key, score, undone, created_at
        FROM fight_history
        WHERE id = $1
        """,
        history_id,
    )
    if not fight:
        await ctx.send(f"❌ Fight History ID **#{history_id}** was not found.")
        return

    rows = await bot.db.fetch(
        """
        SELECT fighter_name, gym_name, division, payout_role, progression_rank,
               amount, status, created_at
        FROM payout_ledger
        WHERE fight_history_id = $1
        ORDER BY CASE WHEN payout_role = 'winner' THEN 0 ELSE 1 END, id
        """,
        history_id,
    )

    if not rows:
        await ctx.send(
            f"📒 **Fight #{history_id} has no payout-ledger entries.**\n"
            "This result may predate the payout-ledger system.\n"
            f"`{PAYOUT_SYSTEM_VERSION}`"
        )
        return

    total = sum(int(row["amount"]) for row in rows if row["status"] == "active")
    embed = discord.Embed(
        title=f"💰 OSBL FIGHT PAYOUT — HISTORY #{history_id}",
        description=(
            f"Type: **{str(fight['fight_type']).title()}** • Score: **{fight['score']}**\n"
            f"Result Status: **{'REVERSED' if fight['undone'] else 'ACTIVE'}**"
        ),
        color=discord.Color.gold(),
    )

    for row in rows:
        role = "🏆 Winner" if row["payout_role"] == "winner" else "🥊 Opponent"
        embed.add_field(
            name=role,
            value=(
                f"**{row['fighter_name']}**\n"
                f"{row['division']} • {row['gym_name']}\n"
                f"Progression: **{row['progression_rank']}**\n"
                f"Payout: **{_money(row['amount'])}**\n"
                f"Ledger Status: **{str(row['status']).upper()}**"
            ),
            inline=False,
        )

    embed.add_field(
        name="💵 Active Fight Payout Total",
        value=f"**{_money(total)}**",
        inline=False,
    )
    embed.set_footer(text=PAYOUT_SYSTEM_VERSION)
    await ctx.send(embed=embed)


@bot.command()
async def fighterpayout(ctx, *, fighter_name: str = None):
    fighter_name = " ".join(str(fighter_name or "").strip().split())
    if not fighter_name:
        await ctx.send("❌ Use: `!fighterpayout Fighter Name`")
        return

    key = fighter_name.casefold()
    fighter = await bot.db.fetchrow(
        """
        SELECT fighter_key, fighter_name, division, gym, career_earnings
        FROM fighters
        WHERE fighter_key = $1
        """,
        key,
    )
    if not fighter:
        await ctx.send(f"❌ Fighter **{fighter_name}** was not found.")
        return

    summary = await bot.db.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'active') AS active_entries,
            COALESCE(SUM(amount) FILTER (WHERE status = 'active'), 0) AS active_total,
            COUNT(*) FILTER (WHERE status = 'reversed') AS reversed_entries,
            COALESCE(SUM(amount) FILTER (WHERE status = 'reversed'), 0) AS reversed_total,
            MAX(created_at) AS latest_payout_at
        FROM payout_ledger
        WHERE fighter_key = $1
        """,
        key,
    )

    recent = await bot.db.fetch(
        """
        SELECT fight_history_id, fight_type, payout_role, amount, status, created_at
        FROM payout_ledger
        WHERE fighter_key = $1
        ORDER BY id DESC
        LIMIT 5
        """,
        key,
    )

    recent_lines = []
    for row in recent:
        recent_lines.append(
            f"#{row['fight_history_id']} • {str(row['fight_type']).title()} • "
            f"{str(row['payout_role']).title()} • **{_money(row['amount'])}** • "
            f"{str(row['status']).upper()}"
        )

    embed = discord.Embed(
        title=f"💵 OSBL FIGHTER PAYOUT PROFILE — {fighter['fighter_name']}",
        description=f"{fighter['division']} • {fighter['gym']}",
        color=discord.Color.gold(),
    )
    bank = await _fighter_bank_snapshot(key)

    embed.add_field(
        name="🏦 Official Career Earnings",
        value=f"**{_money(fighter['career_earnings'])}**",
        inline=True,
    )
    embed.add_field(
        name="✅ Actually Paid Out",
        value=f"**{_money(bank['total_paid_out'])}**",
        inline=True,
    )
    embed.add_field(
        name="🔢 Paid Out Count",
        value=f"**{bank['paid_out_count']}**",
        inline=True,
    )
    embed.add_field(
        name="💵 Current Stacked Balance",
        value=f"**{_money(bank['available_balance'])}**",
        inline=True,
    )
    embed.add_field(
        name="⏳ Pending Payout Requests",
        value=f"**{_money(bank['pending_total'])}**",
        inline=True,
    )
    embed.add_field(
        name="📒 Payout Ledger Since Tracking Began",
        value=(
            f"Active Entries: **{summary['active_entries']}**\n"
            f"Tracked Active Payouts: **{_money(summary['active_total'])}**\n"
            f"Reversed Entries: **{summary['reversed_entries']}**\n"
            f"Reversed Payout Value: **{_money(summary['reversed_total'])}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🧾 Recent Ledger Entries",
        value="\n".join(recent_lines) if recent_lines else "No payout-ledger entries yet.",
        inline=False,
    )
    embed.set_footer(
        text=f"{PAYOUT_SYSTEM_VERSION} • Career earnings remain authoritative for pre-ledger fights"
    )
    await ctx.send(embed=embed)


@bot.command()
async def payoutreport(ctx, session_id: int = None):
    """
    Show financial totals for one fight-night session.
    If no session is supplied, use the newest session.
    """
    if session_id is None:
        session_id = await bot.db.fetchval(
            "SELECT id FROM fight_night_sessions ORDER BY id DESC LIMIT 1"
        )
    if session_id is None:
        await ctx.send("📒 No fight-night sessions exist yet.")
        return

    session = await bot.db.fetchrow(
        """
        SELECT id, status, started_at, ended_at, started_by_name, ended_by_name
        FROM fight_night_sessions
        WHERE id = $1
        """,
        session_id,
    )
    if not session:
        await ctx.send(f"❌ Fight Night Session **#{session_id}** was not found.")
        return

    totals = await bot.db.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'active') AS active_entries,
            COALESCE(SUM(amount) FILTER (WHERE status = 'active'), 0) AS active_total,
            COALESCE(SUM(amount) FILTER (
                WHERE status = 'active' AND payout_role = 'winner'
            ), 0) AS winner_total,
            COALESCE(SUM(amount) FILTER (
                WHERE status = 'active' AND payout_role = 'loser'
            ), 0) AS opponent_total,
            COUNT(*) FILTER (WHERE status = 'reversed') AS reversed_entries,
            COALESCE(SUM(amount) FILTER (WHERE status = 'reversed'), 0) AS reversed_total
        FROM payout_ledger
        WHERE fight_night_session_id = $1
        """,
        session_id,
    )

    gym_rows = await bot.db.fetch(
        """
        SELECT gym_name, COALESCE(SUM(amount), 0) AS gym_total
        FROM payout_ledger
        WHERE fight_night_session_id = $1
          AND status = 'active'
        GROUP BY gym_name
        ORDER BY gym_total DESC, gym_name ASC
        """,
        session_id,
    )

    gym_lines = [
        f"**{row['gym_name']}** — {_money(row['gym_total'])}"
        for row in gym_rows
    ]

    embed = discord.Embed(
        title=f"💰 OSBL FIGHT NIGHT PAYOUT REPORT — SESSION #{session_id}",
        description=f"Session Status: **{str(session['status']).upper()}**",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="💵 Active Payout Totals",
        value=(
            f"Total Paid: **{_money(totals['active_total'])}**\n"
            f"Winner Payouts: **{_money(totals['winner_total'])}**\n"
            f"Opponent Payouts: **{_money(totals['opponent_total'])}**\n"
            f"Ledger Entries: **{totals['active_entries']}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="↩️ Reversed",
        value=(
            f"Entries: **{totals['reversed_entries']}**\n"
            f"Reversed Value: **{_money(totals['reversed_total'])}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏢 Active Payouts by Gym",
        value="\n".join(gym_lines) if gym_lines else "No tracked payouts in this session.",
        inline=False,
    )
    embed.set_footer(text=PAYOUT_SYSTEM_VERSION)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER", "OSBL OFFICIAL")
async def payoutaudit(ctx, limit: int = 10):
    limit = max(1, min(int(limit or 10), 20))
    rows = await bot.db.fetch(
        """
        SELECT id, fight_history_id, fighter_name, gym_name, fight_type,
               payout_role, amount, status, created_at
        FROM payout_ledger
        ORDER BY id DESC
        LIMIT $1
        """,
        limit,
    )
    if not rows:
        await ctx.send(
            "📒 **OSBL PAYOUT AUDIT**\nNo payout-ledger entries exist yet.\n"
            f"`{PAYOUT_SYSTEM_VERSION}`"
        )
        return

    lines = []
    for row in rows:
        lines.append(
            f"**L#{row['id']}** • Fight #{row['fight_history_id']} • "
            f"**{row['fighter_name']}** • {_money(row['amount'])} • "
            f"{str(row['status']).upper()}"
        )

    embed = discord.Embed(
        title="📒 OSBL PAYOUT AUDIT",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{PAYOUT_SYSTEM_VERSION} • Showing newest {len(rows)}")
    await ctx.send(embed=embed)


# =========================================================
# OSBL DATABASE BACKUP SYSTEM
# Commissioner-only manual backup for mobile/iPhone workflow.
# Prefers pg_dump when available; otherwise creates a complete
# compressed logical JSON snapshot of every public table.
# =========================================================

def _osbl_backup_json_value(value):
    """Convert PostgreSQL values into JSON-safe values without losing bytes."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "__osbl_type__": "bytes_base64",
            "data": base64.b64encode(raw).decode("ascii"),
        }
    if isinstance(value, datetime):
        return {
            "__osbl_type__": "datetime",
            "data": value.isoformat(),
        }
    # Decimal, UUID, date/time, inet and other asyncpg-supported values
    # are preserved textually if they are not primitive JSON values.
    return {
        "__osbl_type__": type(value).__name__,
        "data": str(value),
    }


async def _create_osbl_json_backup(path):
    """Create a gzip-compressed logical backup of all public tables."""
    async with bot.db.acquire() as conn:
        table_rows = await conn.fetch(
            """
            SELECT tablename
            FROM pg_catalog.pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
            """
        )
        table_names = [row["tablename"] for row in table_rows]

        payload = {
            "backup_format": "OSBL_LOGICAL_JSON_V1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "database_engine": "PostgreSQL",
            "system_version": DATABASE_BACKUP_VERSION,
            "tables": {},
        }

        for table_name in table_names:
            column_rows = await conn.fetch(
                """
                SELECT
                    column_name,
                    data_type,
                    udt_name,
                    is_nullable,
                    column_default,
                    ordinal_position
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = $1
                ORDER BY ordinal_position
                """,
                table_name,
            )

            # Table names come only from pg_catalog, so quoting them here is safe.
            quoted_table = '"' + table_name.replace('"', '""') + '"'
            data_rows = await conn.fetch(f"SELECT * FROM {quoted_table}")

            payload["tables"][table_name] = {
                "columns": [
                    {
                        "name": col["column_name"],
                        "data_type": col["data_type"],
                        "udt_name": col["udt_name"],
                        "nullable": col["is_nullable"],
                        "default": col["column_default"],
                        "position": col["ordinal_position"],
                    }
                    for col in column_rows
                ],
                "row_count": len(data_rows),
                "rows": [
                    {
                        key: _osbl_backup_json_value(value)
                        for key, value in dict(row).items()
                    }
                    for row in data_rows
                ],
            }

    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    return {
        "format": "Compressed logical JSON",
        "tables": len(payload["tables"]),
        "rows": sum(t["row_count"] for t in payload["tables"].values()),
    }


def _create_osbl_pg_dump(path):
    """Create a native PostgreSQL custom-format dump when pg_dump is installed."""
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        return None

    result = subprocess.run(
        [
            pg_dump,
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--file",
            str(path),
            DATABASE_URL,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "pg_dump failed: " + (result.stderr.strip() or "unknown pg_dump error")
        )

    return {
        "format": "PostgreSQL custom dump",
        "tables": None,
        "rows": None,
    }


@bot.command()
@commands.has_any_role("OSBL COMMISSIONER")
async def databasebackup(ctx):
    """
    Create an on-demand OSBL database backup and send it as a Discord attachment.
    The temporary Railway file is deleted immediately after the send attempt.
    """
    status_message = await ctx.send(
        "💾 **OSBL DATABASE BACKUP STARTED**\n"
        "Creating a protected snapshot of the live PostgreSQL database..."
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    native_path = Path("/tmp") / f"osbl_database_backup_{stamp}.dump"
    json_path = Path("/tmp") / f"osbl_database_backup_{stamp}.json.gz"
    backup_path = None

    try:
        # Prefer a native pg_dump because it is the strongest restore format.
        info = None
        try:
            info = _create_osbl_pg_dump(native_path)
            if info:
                backup_path = native_path
        except Exception as pg_exc:
            print(
                "OSBL pg_dump backup unavailable; falling back to logical JSON: "
                f"{type(pg_exc).__name__}: {pg_exc}"
            )

        # Railway Python images do not always contain PostgreSQL client tools.
        # The fallback still captures every public table and row.
        if backup_path is None:
            info = await _create_osbl_json_backup(json_path)
            backup_path = json_path

        size_bytes = backup_path.stat().st_size
        digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()

        # Discord upload limits vary by server/account. Keep a conservative
        # ceiling and fail clearly rather than silently truncating a backup.
        if size_bytes > 24 * 1024 * 1024:
            await status_message.edit(
                content=(
                    "⚠️ **BACKUP CREATED BUT TOO LARGE FOR DIRECT DISCORD DELIVERY**\n"
                    f"File size: **{size_bytes / (1024 * 1024):.2f} MB**\n"
                    "The database has outgrown the mobile Discord-backup workflow. "
                    "Use Railway/pg_dump storage for this backup."
                )
            )
            return

        description_lines = [
            "✅ **OSBL DATABASE BACKUP COMPLETE**",
            f"Format: **{info['format']}**",
            f"Size: **{size_bytes / 1024:.1f} KB**",
        ]
        if info.get("tables") is not None:
            description_lines.append(f"Tables captured: **{info['tables']}**")
        if info.get("rows") is not None:
            description_lines.append(f"Rows captured: **{info['rows']}**")
        description_lines.extend(
            [
                f"SHA-256: `{digest[:16]}…`",
                "",
                "Save the attached file to **Files / iCloud Drive**.",
                "Do not edit or rename its file extension.",
                f"`{DATABASE_BACKUP_VERSION}`",
            ]
        )

        await ctx.send(
            "\n".join(description_lines),
            file=discord.File(str(backup_path), filename=backup_path.name),
        )

        await status_message.edit(
            content="✅ **OSBL DATABASE BACKUP SENT** — save the attachment to iCloud/Files."
        )

    except Exception as exc:
        print(f"Database backup error: {type(exc).__name__}: {exc}")
        await status_message.edit(
            content=(
                "❌ **OSBL DATABASE BACKUP FAILED**\n"
                f"`{type(exc).__name__}: {exc}`\n"
                "No database records were changed."
            )
        )
    finally:
        for path in (native_path, json_path):
            try:
                if path.exists():
                    path.unlink()
            except Exception as cleanup_exc:
                print(
                    "Temporary backup cleanup warning: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )


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

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dateutil import parser as date_parser

DB_PATH = "diaz_cartel.db"
PAYMENT_WINDOW_HOURS = 24
CURRENCY = "$"

# Optional: Wenn du nur bestimmte Rolle erlauben willst, setz den Namen hier:
STAFF_ROLE_NAME: Optional[str] = None  # z.B. "Cartel-Leitung"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(text: str) -> datetime:
    dt = date_parser.parse(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + f" {CURRENCY}"


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        cur = con.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            sanctions_channel_id INTEGER,
            absences_channel_id INTEGER,
            inventory_channel_id INTEGER,
            botlog_channel_id INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS sanctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            issued_by INTEGER NOT NULL,
            reason TEXT NOT NULL,
            amount INTEGER NOT NULL,
            duration_text TEXT,
            created_at TEXT NOT NULL,
            due_at TEXT NOT NULL,
            paid_at TEXT,
            doubled INTEGER NOT NULL DEFAULT 0
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS absences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_by INTEGER NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            guild_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            qty INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, item)
        )
        """)

        con.commit()


def get_settings(guild_id: int) -> dict:
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM settings WHERE guild_id=?", (guild_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO settings (guild_id) VALUES (?)", (guild_id,))
            con.commit()
            return {
                "sanctions_channel_id": None,
                "absences_channel_id": None,
                "inventory_channel_id": None,
                "botlog_channel_id": None,
            }
        return dict(row)


def set_setting(guild_id: int, key: str, value: Optional[int]):
    with db() as con:
        cur = con.cursor()
        cur.execute("INSERT OR IGNORE INTO settings (guild_id) VALUES (?)", (guild_id,))
        cur.execute(f"UPDATE settings SET {key}=? WHERE guild_id=?", (value, guild_id))
        con.commit()


def staff_check(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    perms = interaction.user.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True
    if STAFF_ROLE_NAME:
        return any(r.name == STAFF_ROLE_NAME for r in interaction.user.roles)
    return False


async def send_to_configured_channel(guild: discord.Guild, setting_key: str, content: str):
    s = get_settings(guild.id)
    channel_id = s.get(setting_key)
    if not channel_id:
        return
    ch = guild.get_channel(int(channel_id))
    if isinstance(ch, discord.TextChannel):
        await ch.send(content)


async def bot_log(guild: discord.Guild, content: str):
    await send_to_configured_channel(guild, "botlog_channel_id", content)


async def try_dm(member: discord.Member, content: str) -> bool:
    try:
        await member.send(content)
        return True
    except Exception:
        return False


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    init_db()
    await bot.tree.sync()
    if not payment_watcher.is_running():
        payment_watcher.start()
    print(f"✅ Diaz Cartel Bot online als {bot.user}")


@tasks.loop(minutes=5)
async def payment_watcher():
    now = utcnow()
    with db() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT id, guild_id, user_id, amount, reason
            FROM sanctions
            WHERE paid_at IS NULL AND doubled=0 AND due_at <= ?
        """, (now.isoformat(),))
        rows = cur.fetchall()

        for r in rows:
            new_amount = int(r["amount"]) * 2
            cur.execute("UPDATE sanctions SET amount=?, doubled=1 WHERE id=?", (new_amount, int(r["id"])))

            guild = bot.get_guild(int(r["guild_id"]))
            if guild:
                msg = (
                    "⚠️ Sanktion automatisch verdoppelt (24h abgelaufen)\n"
                    f"ID: {r['id']}\n"
                    f"User ID: {r['user_id']}\n"
                    f"Neuer Betrag: {money(new_amount)}\n"
                    f"Grund: {r['reason']}"
                )
                try:
                    await send_to_configured_channel(guild, "sanctions_channel_id", msg)
                except Exception:
                    pass

        con.commit()


# ---------------- SETUP COMMANDS ----------------

class SetupGroup(app_commands.Group):
    pass

setup = SetupGroup(name="setup", description="Bot-Setup: Channels setzen (ohne IDs)")

@setup.command(name="sanktionen", description="Channel für Sanktionen setzen")
async def setup_sanktionen(interaction: discord.Interaction, channel: discord.TextChannel):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    set_setting(interaction.guild_id, "sanctions_channel_id", channel.id)
    await interaction.response.send_message(f"✅ Sanktionen-Channel gesetzt: {channel.mention}", ephemeral=True)

@setup.command(name="abmeldungen", description="Channel für Abwesenheiten setzen")
async def setup_abmeldungen(interaction: discord.Interaction, channel: discord.TextChannel):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    set_setting(interaction.guild_id, "absences_channel_id", channel.id)
    await interaction.response.send_message(f"✅ Abmeldungen-Channel gesetzt: {channel.mention}", ephemeral=True)

@setup.command(name="lagerlog", description="Channel für Lager-Logs setzen")
async def setup_lagerlog(interaction: discord.Interaction, channel: discord.TextChannel):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    set_setting(interaction.guild_id, "inventory_channel_id", channel.id)
    await interaction.response.send_message(f"✅ Lager-Log-Channel gesetzt: {channel.mention}", ephemeral=True)

@setup.command(name="botlog", description="Channel für Bot-Logs setzen (optional)")
async def setup_botlog(interaction: discord.Interaction, channel: discord.TextChannel):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    set_setting(interaction.guild_id, "botlog_channel_id", channel.id)
    await interaction.response.send_message(f"✅ Bot-Log-Channel gesetzt: {channel.mention}", ephemeral=True)

@setup.command(name="status", description="Zeigt aktuelle Channel-Einstellungen")
async def setup_status(interaction: discord.Interaction):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    s = get_settings(interaction.guild_id)
    def show(cid):
        if not cid:
            return "nicht gesetzt"
        ch = interaction.guild.get_channel(int(cid))
        return ch.mention if ch else f"ID {cid}"
    msg = (
        "📌 Setup-Status\n"
        f"Sanktionen: {show(s.get('sanctions_channel_id'))}\n"
        f"Abmeldungen: {show(s.get('absences_channel_id'))}\n"
        f"Lager-Log: {show(s.get('inventory_channel_id'))}\n"
        f"Bot-Log: {show(s.get('botlog_channel_id'))}\n"
    )
    await interaction.response.send_message(msg, ephemeral=True)


# ---------------- SANKTIONEN ----------------

class SanctionGroup(app_commands.Group):
    pass

sanction = SanctionGroup(name="sanktion", description="Sanktionen verwalten")

@sanction.command(name="ausstellen", description="Sanktion ausstellen (Zahlung in 24h, sonst Verdopplung)")
@app_commands.describe(member="Wer?", betrag="0 bis 100000", dauer="z.B. '3 Tage'", grund="Warum?")
async def sanction_issue(interaction: discord.Interaction, member: discord.Member, betrag: int, dauer: str, grund: str):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

    if betrag < 0 or betrag > 100000:
        return await interaction.response.send_message("❌ Betrag muss zwischen 0 und 100000 liegen.", ephemeral=True)

    created = utcnow()
    due = created + timedelta(hours=PAYMENT_WINDOW_HOURS)

    with db() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO sanctions (guild_id, user_id, issued_by, reason, amount, duration_text, created_at, due_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (interaction.guild_id, member.id, interaction.user.id, grund, betrag, dauer, created.isoformat(), due.isoformat()))
        sid = cur.lastrowid
        con.commit()

    await interaction.response.send_message(f"✅ Sanktion erstellt. ID {sid}.", ephemeral=True)

    if interaction.guild:
        public_msg = (
            "📄 Neue Sanktion\n"
            f"ID: {sid}\n"
            f"Person: {member.mention}\n"
            f"Aussteller: {interaction.user.mention}\n"
            f"Grund: {grund}\n"
            f"Betrag: {money(betrag)}\n"
            f"Dauer: {dauer}\n"
            f"Zahlungsfrist: 24 Stunden (bis {due.strftime('%Y-%m-%d %H:%M UTC')})\n"
            "Hinweis: Nach Ablauf verdoppelt sich der Betrag automatisch."
        )
        await send_to_configured_channel(interaction.guild, "sanctions_channel_id", public_msg)

    dm_msg = (
        "Du hast eine Sanktion erhalten.\n"
        f"ID: {sid}\n"
        f"Grund: {grund}\n"
        f"Betrag: {money(betrag)}\n"
        f"Dauer: {dauer}\n"
        f"Zahlungsfrist: 24 Stunden (bis {due.strftime('%Y-%m-%d %H:%M UTC')})\n"
        "Wichtig: Nach Ablauf verdoppelt sich der Betrag automatisch."
    )
    ok = await try_dm(member, dm_msg)
    if not ok and interaction.guild:
        await bot_log(interaction.guild, f"⚠️ Konnte keine DM an {member} senden (ID {member.id}).")

@sanction.command(name="bezahlt", description="Sanktion als bezahlt markieren")
@app_commands.describe(id="Sanktions-ID")
async def sanction_paid(interaction: discord.Interaction, id: int):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM sanctions WHERE id=? AND guild_id=?", (id, interaction.guild_id))
        row = cur.fetchone()
        if not row:
            return await interaction.response.send_message("❌ ID nicht gefunden.", ephemeral=True)
        if row["paid_at"]:
            return await interaction.response.send_message("ℹ️ Schon bezahlt markiert.", ephemeral=True)

        cur.execute("UPDATE sanctions SET paid_at=? WHERE id=?", (utcnow().isoformat(), id))
        con.commit()

    await interaction.response.send_message(f"✅ Sanktion ID {id} als bezahlt markiert.", ephemeral=True)

    if interaction.guild:
        await send_to_configured_channel(
            interaction.guild,
            "sanctions_channel_id",
            f"✅ Sanktion bezahlt\nID: {id}\nMarkiert von: {interaction.user.mention}"
        )

@sanction.command(name="liste", description="Letzte Sanktionen anzeigen")
@app_commands.describe(nur_offen="Nur offene Sanktionen?")
async def sanction_list(interaction: discord.Interaction, nur_offen: bool = True):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

    with db() as con:
        cur = con.cursor()
        if nur_offen:
            cur.execute("""
                SELECT * FROM sanctions
                WHERE guild_id=? AND paid_at IS NULL
                ORDER BY id DESC LIMIT 20
            """, (interaction.guild_id,))
        else:
            cur.execute("""
                SELECT * FROM sanctions
                WHERE guild_id=?
                ORDER BY id DESC LIMIT 20
            """, (interaction.guild_id,))
        rows = cur.fetchall()

    if not rows:
        return await interaction.response.send_message("ℹ️ Keine Sanktionen gefunden.", ephemeral=True)

    out = ["Sanktionen:"]
    for r in rows:
        out.append(f"ID {r['id']} | User {r['user_id']} | {money(int(r['amount']))} | Grund: {r['reason']}")
    await interaction.response.send_message("\n".join(out), ephemeral=True)


# ---------------- ABWESENHEIT ----------------

class AbsenceGroup(app_commands.Group):
    pass

absence = AbsenceGroup(name="abwesenheit", description="Abwesenheit eintragen")

@absence.command(name="eintragen", description="Abwesenheit eintragen")
@app_commands.describe(start="z.B. 2026-01-28 18:00", ende="z.B. 2026-02-01 18:00", grund="optional")
async def absence_add(interaction: discord.Interaction, start: str, ende: str, grund: str = ""):
    if not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("❌ Fehler.", ephemeral=True)

    try:
        sdt = parse_dt(start)
        edt = parse_dt(ende)
    except Exception:
        return await interaction.response.send_message("❌ Datum nicht verstanden. Beispiel: 2026-01-28 18:00", ephemeral=True)

    if edt <= sdt:
        return await interaction.response.send_message("❌ Ende muss nach Start liegen.", ephemeral=True)

    with db() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO absences (guild_id, user_id, created_by, start_at, end_at, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (interaction.guild_id, interaction.user.id, interaction.user.id, sdt.isoformat(), edt.isoformat(), grund, utcnow().isoformat()))
        aid = cur.lastrowid
        con.commit()

    await interaction.response.send_message("✅ Abwesenheit eingetragen.", ephemeral=True)

    if interaction.guild:
        msg = (
            "📌 Abwesenheit\n"
            f"User: {interaction.user.mention}\n"
            f"Von: {sdt.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Bis: {edt.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Grund: {grund or '-'}\n"
            f"ID: {aid}"
        )
        await send_to_configured_channel(interaction.guild, "absences_channel_id", msg)

@absence.command(name="liste", description="Abwesenheiten anzeigen")
async def absence_list(interaction: discord.Interaction):
    with db() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM absences
            WHERE guild_id=?
            ORDER BY start_at ASC LIMIT 20
        """, (interaction.guild_id,))
        rows = cur.fetchall()

    if not rows:
        return await interaction.response.send_message("ℹ️ Keine Abwesenheiten.", ephemeral=True)

    out = ["Abwesenheiten:"]
    for r in rows:
        sdt = datetime.fromisoformat(r["start_at"]).astimezone(timezone.utc)
        edt = datetime.fromisoformat(r["end_at"]).astimezone(timezone.utc)
        out.append(f"User {r['user_id']} | {sdt.strftime('%Y-%m-%d %H:%M')} bis {edt.strftime('%Y-%m-%d %H:%M')} UTC | {r['reason'] or '-'}")
    await interaction.response.send_message("\n".join(out), ephemeral=True)


# ---------------- LAGER ----------------

class InventoryGroup(app_commands.Group):
    pass

lager = InventoryGroup(name="lager", description="Lagerbestand verwalten")

@lager.command(name="setzen", description="Item-Menge setzen")
@app_commands.describe(item="Item-Name", menge=">= 0")
async def inv_set(interaction: discord.Interaction, item: str, menge: int):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    if menge < 0:
        return await interaction.response.send_message("❌ Menge muss >= 0 sein.", ephemeral=True)

    key = item.strip().lower()
    with db() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO inventory (guild_id, item, qty, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, item) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at
        """, (interaction.guild_id, key, menge, utcnow().isoformat()))
        con.commit()

    await interaction.response.send_message("✅ Lager aktualisiert.", ephemeral=True)
    if interaction.guild:
        await send_to_configured_channel(interaction.guild, "inventory_channel_id",
                                         f"📦 Lager SET | {interaction.user.mention} | {item} = {menge}")

@lager.command(name="hinzu", description="Item erhöhen")
@app_commands.describe(item="Item-Name", menge="> 0")
async def inv_add(interaction: discord.Interaction, item: str, menge: int):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    if menge <= 0:
        return await interaction.response.send_message("❌ Menge muss > 0 sein.", ephemeral=True)

    key = item.strip().lower()
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT qty FROM inventory WHERE guild_id=? AND item=?", (interaction.guild_id, key))
        row = cur.fetchone()
        current = int(row["qty"]) if row else 0
        new_qty = current + menge
        cur.execute("""
            INSERT INTO inventory (guild_id, item, qty, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, item) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at
        """, (interaction.guild_id, key, new_qty, utcnow().isoformat()))
        con.commit()

    await interaction.response.send_message("✅ Lager erhöht.", ephemeral=True)
    if interaction.guild:
        await send_to_configured_channel(interaction.guild, "inventory_channel_id",
                                         f"📦 Lager ADD | {interaction.user.mention} | {item} +{menge} (neu {new_qty})")

@lager.command(name="entfernen", description="Item verringern")
@app_commands.describe(item="Item-Name", menge="> 0")
async def inv_remove(interaction: discord.Interaction, item: str, menge: int):
    if not staff_check(interaction):
        return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
    if menge <= 0:
        return await interaction.response.send_message("❌ Menge muss > 0 sein.", ephemeral=True)

    key = item.strip().lower()
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT qty FROM inventory WHERE guild_id=? AND item=?", (interaction.guild_id, key))
        row = cur.fetchone()
        if not row:
            return await interaction.response.send_message("❌ Item nicht im Lager.", ephemeral=True)
        current = int(row["qty"])
        new_qty = max(0, current - menge)
        cur.execute("UPDATE inventory SET qty=?, updated_at=? WHERE guild_id=? AND item=?",
                    (new_qty, utcnow().isoformat(), interaction.guild_id, key))
        con.commit()

    await interaction.response.send_message("✅ Lager reduziert.", ephemeral=True)
    if interaction.guild:
        await send_to_configured_channel(interaction.guild, "inventory_channel_id",
                                         f"📦 Lager REMOVE | {interaction.user.mention} | {item} -{menge} (neu {new_qty})")

@lager.command(name="liste", description="Lagerbestand anzeigen")
async def inv_list(interaction: discord.Interaction):
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT item, qty FROM inventory WHERE guild_id=? ORDER BY item ASC", (interaction.guild_id,))
        rows = cur.fetchall()

    if not rows:
        return await interaction.response.send_message("ℹ️ Lager ist leer.", ephemeral=True)

    out = ["Lagerbestand:"]
    for r in rows:
        out.append(f"{r['item']}: {r['qty']}")
    await interaction.response.send_message("\n".join(out), ephemeral=True)


# Register
bot.tree.add_command(setup)
bot.tree.add_command(sanction)
bot.tree.add_command(absence)
bot.tree.add_command(lager)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN fehlt. In Railway als Variable setzen.")
    bot.run(token)

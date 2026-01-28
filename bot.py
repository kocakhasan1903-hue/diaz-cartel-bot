import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from dateutil import parser as date_parser

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = "diaz_cartel.db"

# ---------------- DB ----------------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        cur = con.cursor()

        # Speichert die Message IDs der Panels pro Guild
        cur.execute("""
        CREATE TABLE IF NOT EXISTS panels (
            guild_id INTEGER PRIMARY KEY,
            sanctions_channel_id INTEGER,
            sanctions_message_id INTEGER,
            absences_channel_id INTEGER,
            absences_message_id INTEGER,
            inventory_channel_id INTEGER,
            inventory_message_id INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS sanctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            amount INTEGER NOT NULL,
            duration TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS absences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            reason TEXT NOT NULL,
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

def upsert_panel(guild_id: int, key_channel: str, key_msg: str, channel_id: int, message_id: int):
    with db() as con:
        cur = con.cursor()
        cur.execute("INSERT OR IGNORE INTO panels (guild_id) VALUES (?)", (guild_id,))
        cur.execute(f"UPDATE panels SET {key_channel}=?, {key_msg}=? WHERE guild_id=?",
                    (channel_id, message_id, guild_id))
        con.commit()

# ---------------- Helpers ----------------
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def parse_dt(text: str) -> str:
    dt = date_parser.parse(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")

def money(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " $"

def clamp_lines(lines, max_lines=20):
    return lines[:max_lines]

# ---------------- Embeds (TABELLEN) ----------------
def sanctions_embed(guild_id: int) -> discord.Embed:
    with db() as con:
        rows = con.execute(
            "SELECT * FROM sanctions WHERE guild_id=? ORDER BY id DESC",
            (guild_id,)
        ).fetchall()

    header = "ID | Name | Betrag | Status\n"
    lines = []
    for r in rows:
        icon = "✅" if r["status"] == "bezahlt" else "❌"
        lines.append(f"{r['id']} | {r['name']} | {money(int(r['amount']))} | {icon}")

    if not lines:
        body = "Keine Sanktionen vorhanden."
    else:
        body = header + "\n".join(clamp_lines(lines, 25))  # max 25 Zeilen

    embed = discord.Embed(
        title="📕 DÍAZ CARTEL – SANKTIONEN",
        description=f"```{body}```",
        color=0x0D47A1
    )
    embed.set_footer(text="Buttons benutzen: Hinzufügen / Bezahlt / Offen / Löschen")
    return embed

def absences_embed(guild_id: int) -> discord.Embed:
    with db() as con:
        rows = con.execute(
            "SELECT * FROM absences WHERE guild_id=? ORDER BY id DESC",
            (guild_id,)
        ).fetchall()

    header = "ID | Name | Von | Bis\n"
    lines = []
    for r in rows:
        lines.append(f"{r['id']} | {r['name']} | {r['start_at']} | {r['end_at']}")

    if not lines:
        body = "Keine Abwesenheiten vorhanden."
    else:
        body = header + "\n".join(clamp_lines(lines, 25))

    embed = discord.Embed(
        title="📌 DÍAZ CARTEL – ABWESENHEITEN",
        description=f"```{body}```",
        color=0x1B5E20
    )
    embed.set_footer(text="Buttons benutzen: Eintragen / Entfernen")
    return embed

def inventory_embed(guild_id: int) -> discord.Embed:
    with db() as con:
        rows = con.execute(
            "SELECT item, qty FROM inventory WHERE guild_id=? ORDER BY item ASC",
            (guild_id,)
        ).fetchall()

    header = "Item | Menge\n"
    lines = []
    for r in rows:
        lines.append(f"{r['item']} | {r['qty']}")

    if not lines:
        body = "Lager ist leer."
    else:
        body = header + "\n".join(clamp_lines(lines, 30))

    embed = discord.Embed(
        title="📦 DÍAZ CARTEL – LAGER",
        description=f"```{body}```",
        color=0x263238
    )
    embed.set_footer(text="Buttons benutzen: Hinzufügen / Entfernen / Setzen")
    return embed

# ---------------- Modals ----------------
class AddSanctionModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message):
        super().__init__(title="Sanktion hinzufügen")
        self.guild_id = guild_id
        self.message = message

        self.name = TextInput(label="Name (IC/Spieler)", placeholder="z.B. Luciano", max_length=50)
        self.amount = TextInput(label="Betrag (0 - 100000)", placeholder="z.B. 5000", max_length=6)
        self.duration = TextInput(label="Dauer", placeholder="z.B. 3 Tage", max_length=30)
        self.reason = TextInput(label="Grund", placeholder="z.B. Respektlosigkeit", max_length=200)

        self.add_item(self.name)
        self.add_item(self.amount)
        self.add_item(self.duration)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amt = int(str(self.amount.value).strip())
        except:
            return await interaction.response.send_message("❌ Betrag muss eine Zahl sein.", ephemeral=True)

        if amt < 0 or amt > 100000:
            return await interaction.response.send_message("❌ Betrag muss zwischen 0 und 100000 sein.", ephemeral=True)

        with db() as con:
            con.execute("""
                INSERT INTO sanctions (guild_id, name, amount, duration, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (self.guild_id, self.name.value.strip(), amt, self.duration.value.strip(),
                  self.reason.value.strip(), "offen", now_iso()))
            con.commit()

        await self.message.edit(embed=sanctions_embed(self.guild_id), view=SanctionsView(self.guild_id))
        await interaction.response.send_message("✅ Sanktion eingetragen.", ephemeral=True)

class ChangeSanctionStatusModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message, new_status: str):
        super().__init__(title=f"Sanktion -> {new_status}")
        self.guild_id = guild_id
        self.message = message
        self.new_status = new_status

        self.sid = TextInput(label="Sanktions-ID", placeholder="z.B. 1", max_length=10)
        self.add_item(self.sid)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            sid = int(self.sid.value.strip())
        except:
            return await interaction.response.send_message("❌ ID muss eine Zahl sein.", ephemeral=True)

        with db() as con:
            cur = con.execute("SELECT id FROM sanctions WHERE guild_id=? AND id=?", (self.guild_id, sid)).fetchone()
            if not cur:
                return await interaction.response.send_message("❌ ID nicht gefunden.", ephemeral=True)
            con.execute("UPDATE sanctions SET status=? WHERE guild_id=? AND id=?",
                        (self.new_status, self.guild_id, sid))
            con.commit()

        await self.message.edit(embed=sanctions_embed(self.guild_id), view=SanctionsView(self.guild_id))
        await interaction.response.send_message("✅ Status geändert.", ephemeral=True)

class DeleteSanctionModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message):
        super().__init__(title="Sanktion löschen")
        self.guild_id = guild_id
        self.message = message
        self.sid = TextInput(label="Sanktions-ID", placeholder="z.B. 1", max_length=10)
        self.add_item(self.sid)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            sid = int(self.sid.value.strip())
        except:
            return await interaction.response.send_message("❌ ID muss eine Zahl sein.", ephemeral=True)

        with db() as con:
            con.execute("DELETE FROM sanctions WHERE guild_id=? AND id=?", (self.guild_id, sid))
            con.commit()

        await self.message.edit(embed=sanctions_embed(self.guild_id), view=SanctionsView(self.guild_id))
        await interaction.response.send_message("✅ Gelöscht.", ephemeral=True)

class AddAbsenceModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message):
        super().__init__(title="Abwesenheit eintragen")
        self.guild_id = guild_id
        self.message = message

        self.name = TextInput(label="Name (IC/Spieler)", placeholder="z.B. Hassan", max_length=50)
        self.start = TextInput(label="Von (Datum/Uhrzeit)", placeholder="2026-01-28 18:00", max_length=40)
        self.end = TextInput(label="Bis (Datum/Uhrzeit)", placeholder="2026-01-30 18:00", max_length=40)
        self.reason = TextInput(label="Grund", placeholder="z.B. Urlaub / Arbeit", max_length=120)

        self.add_item(self.name)
        self.add_item(self.start)
        self.add_item(self.end)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            s = parse_dt(self.start.value.strip())
            e = parse_dt(self.end.value.strip())
        except:
            return await interaction.response.send_message("❌ Datum nicht verstanden. Beispiel: 2026-01-28 18:00", ephemeral=True)

        with db() as con:
            con.execute("""
                INSERT INTO absences (guild_id, name, start_at, end_at, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (self.guild_id, self.name.value.strip(), s, e, self.reason.value.strip(), now_iso()))
            con.commit()

        await self.message.edit(embed=absences_embed(self.guild_id), view=AbsencesView(self.guild_id))
        await interaction.response.send_message("✅ Abwesenheit eingetragen.", ephemeral=True)

class DeleteAbsenceModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message):
        super().__init__(title="Abwesenheit entfernen")
        self.guild_id = guild_id
        self.message = message
        self.aid = TextInput(label="Abwesenheits-ID", placeholder="z.B. 1", max_length=10)
        self.add_item(self.aid)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            aid = int(self.aid.value.strip())
        except:
            return await interaction.response.send_message("❌ ID muss eine Zahl sein.", ephemeral=True)

        with db() as con:
            con.execute("DELETE FROM absences WHERE guild_id=? AND id=?", (self.guild_id, aid))
            con.commit()

        await self.message.edit(embed=absences_embed(self.guild_id), view=AbsencesView(self.guild_id))
        await interaction.response.send_message("✅ Entfernt.", ephemeral=True)

class InventoryChangeModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message, mode: str):
        super().__init__(title=f"Lager: {mode}")
        self.guild_id = guild_id
        self.message = message
        self.mode = mode  # "hinzu" / "entfernen" / "setzen"

        self.item = TextInput(label="Item", placeholder="z.B. medikits", max_length=40)
        self.qty = TextInput(label="Menge", placeholder="z.B. 10", max_length=10)
        self.add_item(self.item)
        self.add_item(self.qty)

    async def on_submit(self, interaction: discord.Interaction):
        key = self.item.value.strip().lower()
        try:
            q = int(self.qty.value.strip())
        except:
            return await interaction.response.send_message("❌ Menge muss eine Zahl sein.", ephemeral=True)

        with db() as con:
            cur = con.execute("SELECT qty FROM inventory WHERE guild_id=? AND item=?", (self.guild_id, key)).fetchone()
            current = int(cur["qty"]) if cur else 0

            if self.mode == "hinzu":
                new_qty = current + q
            elif self.mode == "entfernen":
                new_qty = max(0, current - q)
            else:  # setzen
                new_qty = max(0, q)

            con.execute("""
                INSERT INTO inventory (guild_id, item, qty, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, item) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at
            """, (self.guild_id, key, new_qty, now_iso()))
            con.commit()

        await self.message.edit(embed=inventory_embed(self.guild_id), view=InventoryView(self.guild_id))
        await interaction.response.send_message("✅ Lager aktualisiert.", ephemeral=True)

# ---------------- Views (BUTTONS) ----------------
class SanctionsView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ Sanktion hinzufügen", style=discord.ButtonStyle.primary)
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddSanctionModal(self.guild_id, interaction.message))

    @discord.ui.button(label="✅ Als bezahlt markieren", style=discord.ButtonStyle.success)
    async def paid(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ChangeSanctionStatusModal(self.guild_id, interaction.message, "bezahlt"))

    @discord.ui.button(label="❌ Als offen markieren", style=discord.ButtonStyle.secondary)
    async def open_(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ChangeSanctionStatusModal(self.guild_id, interaction.message, "offen"))

    @discord.ui.button(label="🗑️ Sanktion löschen", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(DeleteSanctionModal(self.guild_id, interaction.message))

class AbsencesView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ Abwesenheit eintragen", style=discord.ButtonStyle.primary)
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddAbsenceModal(self.guild_id, interaction.message))

    @discord.ui.button(label="🗑️ Abwesenheit entfernen", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(DeleteAbsenceModal(self.guild_id, interaction.message))

class InventoryView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ Hinzufügen", style=discord.ButtonStyle.success)
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryChangeModal(self.guild_id, interaction.message, "hinzu"))

    @discord.ui.button(label="➖ Entfernen", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryChangeModal(self.guild_id, interaction.message, "entfernen"))

    @discord.ui.button(label="🧾 Setzen", style=discord.ButtonStyle.secondary)
    async def set_(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryChangeModal(self.guild_id, interaction.message, "setzen"))

# ---------------- Bot Setup ----------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

class PanelGroup(app_commands.Group):
    pass

panel = PanelGroup(name="panel", description="Panels erstellen (Tabellen sichtbar im Channel)")

@panel.command(name="sanktionen", description="Erstellt das Sanktionen-Panel in diesem Channel")
async def panel_sanktionen(interaction: discord.Interaction):
    embed = sanctions_embed(interaction.guild_id)
    view = SanctionsView(interaction.guild_id)
    msg = await interaction.channel.send(embed=embed, view=view)
    upsert_panel(interaction.guild_id, "sanctions_channel_id", "sanctions_message_id", interaction.channel_id, msg.id)
    await interaction.response.send_message("✅ Sanktionen-Panel erstellt.", ephemeral=True)

@panel.command(name="abwesenheit", description="Erstellt das Abwesenheiten-Panel in diesem Channel")
async def panel_abwesenheit(interaction: discord.Interaction):
    embed = absences_embed(interaction.guild_id)
    view = AbsencesView(interaction.guild_id)
    msg = await interaction.channel.send(embed=embed, view=view)
    upsert_panel(interaction.guild_id, "absences_channel_id", "absences_message_id", interaction.channel_id, msg.id)
    await interaction.response.send_message("✅ Abwesenheits-Panel erstellt.", ephemeral=True)

@panel.command(name="lager", description="Erstellt das Lager-Panel in diesem Channel")
async def panel_lager(interaction: discord.Interaction):
    embed = inventory_embed(interaction.guild_id)
    view = InventoryView(interaction.guild_id)
    msg = await interaction.channel.send(embed=embed, view=view)
    upsert_panel(interaction.guild_id, "inventory_channel_id", "inventory_message_id", interaction.channel_id, msg.id)
    await interaction.response.send_message("✅ Lager-Panel erstellt.", ephemeral=True)

@bot.event
async def on_ready():
    init_db()
    bot.tree.add_command(panel)
    await bot.tree.sync()
    print(f"✅ Bot online als {bot.user}")

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN fehlt (Railway Variables).")

bot.run(TOKEN)

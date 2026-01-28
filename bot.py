import os
import sqlite3
from datetime import datetime, timezone
from typing import List

import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from dateutil import parser as date_parser

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = "diaz_cartel.db"

# ---------- Discord ----------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DB ----------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    with db() as con:
        cur = con.cursor()

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

def parse_dt(text: str) -> str:
    dt = date_parser.parse(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")

def money(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " $"

def clamp(lines: List[str], n=25):
    return lines[:n]

# ---------- Embeds (TABELLEN) ----------
def sanctions_embed(guild_id: int) -> discord.Embed:
    with db() as con:
        rows = con.execute("SELECT * FROM sanctions WHERE guild_id=? ORDER BY id DESC", (guild_id,)).fetchall()

    header = "ID | Name | Betrag | Status\n"
    lines = []
    for r in rows:
        icon = "✅" if r["status"] == "bezahlt" else "❌"
        lines.append(f"{r['id']} | {r['name']} | {money(int(r['amount']))} | {icon}")

    body = "Keine Sanktionen vorhanden." if not lines else header + "\n".join(clamp(lines, 25))

    e = discord.Embed(title="📕 DÍAZ CARTEL – SANKTIONEN", description=f"```{body}```", color=0x0D47A1)
    e.set_footer(text="Buttons: Hinzufügen | Bezahlt | Offen | Löschen")
    return e

def absences_embed(guild_id: int) -> discord.Embed:
    with db() as con:
        rows = con.execute("SELECT * FROM absences WHERE guild_id=? ORDER BY id DESC", (guild_id,)).fetchall()

    header = "ID | Name | Von | Bis\n"
    lines = []
    for r in rows:
        lines.append(f"{r['id']} | {r['name']} | {r['start_at']} | {r['end_at']}")

    body = "Keine Abwesenheiten vorhanden." if not lines else header + "\n".join(clamp(lines, 25))

    e = discord.Embed(title="📌 DÍAZ CARTEL – ABWESENHEITEN", description=f"```{body}```", color=0x1B5E20)
    e.set_footer(text="Buttons: Eintragen | Entfernen")
    return e

def inventory_embed(guild_id: int) -> discord.Embed:
    with db() as con:
        rows = con.execute("SELECT item, qty FROM inventory WHERE guild_id=? ORDER BY item ASC", (guild_id,)).fetchall()

    header = "Item | Menge\n"
    lines = []
    for r in rows:
        lines.append(f"{r['item']} | {r['qty']}")

    body = "Lager ist leer." if not lines else header + "\n".join(clamp(lines, 30))

    e = discord.Embed(title="📦 DÍAZ CARTEL – LAGER", description=f"```{body}```", color=0x263238)
    e.set_footer(text="Buttons: Setzen | Add | Remove")
    return e

# ---------- Modals ----------
class AddSanctionModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message):
        super().__init__(title="Sanktion hinzufügen")
        self.guild_id = guild_id
        self.message = message

        self.name = TextInput(label="Name", placeholder="z.B. Luciano", max_length=50)
        self.amount = TextInput(label="Betrag (0-100000)", placeholder="5000", max_length=6)
        self.duration = TextInput(label="Dauer", placeholder="z.B. 3 Tage", max_length=30)
        self.reason = TextInput(label="Grund", placeholder="z.B. Respektlosigkeit", max_length=200)

        self.add_item(self.name)
        self.add_item(self.amount)
        self.add_item(self.duration)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amt = int(self.amount.value.strip())
        except:
            return await interaction.response.send_message("❌ Betrag muss Zahl sein.", ephemeral=True)
        if amt < 0 or amt > 100000:
            return await interaction.response.send_message("❌ Betrag muss 0-100000 sein.", ephemeral=True)

        with db() as con:
            con.execute("""
                INSERT INTO sanctions (guild_id, name, amount, duration, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (self.guild_id, self.name.value.strip(), amt, self.duration.value.strip(),
                  self.reason.value.strip(), "offen", now_iso()))
            con.commit()

        await self.message.edit(embed=sanctions_embed(self.guild_id), view=SanctionsView(self.guild_id))
        await interaction.response.send_message("✅ Eingetragen.", ephemeral=True)

class SanctionIdModal(Modal):
    def __init__(self, title: str, guild_id: int, message: discord.Message, mode: str):
        super().__init__(title=title)
        self.guild_id = guild_id
        self.message = message
        self.mode = mode  # paid/open/delete

        self.sid = TextInput(label="Sanktions-ID", placeholder="z.B. 1", max_length=10)
        self.add_item(self.sid)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            sid = int(self.sid.value.strip())
        except:
            return await interaction.response.send_message("❌ ID muss Zahl sein.", ephemeral=True)

        with db() as con:
            exists = con.execute("SELECT id FROM sanctions WHERE guild_id=? AND id=?", (self.guild_id, sid)).fetchone()
            if not exists:
                return await interaction.response.send_message("❌ ID nicht gefunden.", ephemeral=True)

            if self.mode == "paid":
                con.execute("UPDATE sanctions SET status='bezahlt' WHERE guild_id=? AND id=?", (self.guild_id, sid))
            elif self.mode == "open":
                con.execute("UPDATE sanctions SET status='offen' WHERE guild_id=? AND id=?", (self.guild_id, sid))
            elif self.mode == "delete":
                con.execute("DELETE FROM sanctions WHERE guild_id=? AND id=?", (self.guild_id, sid))
            con.commit()

        await self.message.edit(embed=sanctions_embed(self.guild_id), view=SanctionsView(self.guild_id))
        await interaction.response.send_message("✅ Aktualisiert.", ephemeral=True)

class AddAbsenceModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message):
        super().__init__(title="Abwesenheit eintragen")
        self.guild_id = guild_id
        self.message = message

        self.name = TextInput(label="Name", placeholder="z.B. Hassan", max_length=50)
        self.start = TextInput(label="Von (Datum/Zeit)", placeholder="2026-01-28 18:00", max_length=40)
        self.end = TextInput(label="Bis (Datum/Zeit)", placeholder="2026-01-30 18:00", max_length=40)
        self.reason = TextInput(label="Grund", placeholder="Urlaub/Arbeit", max_length=120)

        self.add_item(self.name)
        self.add_item(self.start)
        self.add_item(self.end)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            s = parse_dt(self.start.value.strip())
            e = parse_dt(self.end.value.strip())
        except:
            return await interaction.response.send_message("❌ Datum falsch. Beispiel: 2026-01-28 18:00", ephemeral=True)

        with db() as con:
            con.execute("""
                INSERT INTO absences (guild_id, name, start_at, end_at, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (self.guild_id, self.name.value.strip(), s, e, self.reason.value.strip(), now_iso()))
            con.commit()

        await self.message.edit(embed=absences_embed(self.guild_id), view=AbsencesView(self.guild_id))
        await interaction.response.send_message("✅ Eingetragen.", ephemeral=True)

class AbsenceDeleteModal(Modal):
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
            return await interaction.response.send_message("❌ ID muss Zahl sein.", ephemeral=True)

        with db() as con:
            con.execute("DELETE FROM absences WHERE guild_id=? AND id=?", (self.guild_id, aid))
            con.commit()

        await self.message.edit(embed=absences_embed(self.guild_id), view=AbsencesView(self.guild_id))
        await interaction.response.send_message("✅ Entfernt.", ephemeral=True)

class InventoryModal(Modal):
    def __init__(self, guild_id: int, message: discord.Message, mode: str):
        super().__init__(title=f"Lager: {mode}")
        self.guild_id = guild_id
        self.message = message
        self.mode = mode  # set/add/remove

        self.item = TextInput(label="Item", placeholder="medikits", max_length=40)
        self.qty = TextInput(label="Menge", placeholder="10", max_length=10)
        self.add_item(self.item)
        self.add_item(self.qty)

    async def on_submit(self, interaction: discord.Interaction):
        item = self.item.value.strip().lower()
        try:
            q = int(self.qty.value.strip())
        except:
            return await interaction.response.send_message("❌ Menge muss Zahl sein.", ephemeral=True)

        with db() as con:
            cur = con.execute("SELECT qty FROM inventory WHERE guild_id=? AND item=?", (self.guild_id, item)).fetchone()
            current = int(cur["qty"]) if cur else 0

            if self.mode == "set":
                new_qty = max(0, q)
            elif self.mode == "add":
                new_qty = current + max(0, q)
            else:
                new_qty = max(0, current - max(0, q))

            con.execute("""
                INSERT INTO inventory (guild_id, item, qty, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, item) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at
            """, (self.guild_id, item, new_qty, now_iso()))
            con.commit()

        await self.message.edit(embed=inventory_embed(self.guild_id), view=InventoryView(self.guild_id))
        await interaction.response.send_message("✅ Lager aktualisiert.", ephemeral=True)

# ---------- Views ----------
class SanctionsView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ Hinzufügen", style=discord.ButtonStyle.primary)
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddSanctionModal(self.guild_id, interaction.message))

    @discord.ui.button(label="✅ Bezahlt", style=discord.ButtonStyle.success)
    async def paid(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SanctionIdModal("Sanktion: Bezahlt", self.guild_id, interaction.message, "paid"))

    @discord.ui.button(label="❌ Offen", style=discord.ButtonStyle.secondary)
    async def open_(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SanctionIdModal("Sanktion: Offen", self.guild_id, interaction.message, "open"))

    @discord.ui.button(label="🗑️ Löschen", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SanctionIdModal("Sanktion: Löschen", self.guild_id, interaction.message, "delete"))

class AbsencesView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ Eintragen", style=discord.ButtonStyle.primary)
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddAbsenceModal(self.guild_id, interaction.message))

    @discord.ui.button(label="🗑️ Entfernen", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AbsenceDeleteModal(self.guild_id, interaction.message))

class InventoryView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="🧾 Setzen", style=discord.ButtonStyle.secondary)
    async def set_(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryModal(self.guild_id, interaction.message, "set"))

    @discord.ui.button(label="➕ Add", style=discord.ButtonStyle.success)
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryModal(self.guild_id, interaction.message, "add"))

    @discord.ui.button(label="➖ Remove", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryModal(self.guild_id, interaction.message, "remove"))

# ---------- Slash Commands ----------
class PanelGroup(app_commands.Group):
    pass

panel = PanelGroup(name="panel", description="Panels erstellen")

@panel.command(name="sanktionen", description="Sanktionen Panel in diesem Channel erstellen")
async def panel_sanktionen(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    msg = await interaction.channel.send(embed=sanctions_embed(interaction.guild_id), view=SanctionsView(interaction.guild_id))
    upsert_panel(interaction.guild_id, "sanctions_channel_id", "sanctions_message_id", interaction.channel_id, msg.id)
    await interaction.followup.send("✅ Sanktionen-Panel erstellt.", ephemeral=True)

@panel.command(name="abwesenheit", description="Abwesenheiten Panel in diesem Channel erstellen")
async def panel_abwesenheit(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    msg = await interaction.channel.send(embed=absences_embed(interaction.guild_id), view=AbsencesView(interaction.guild_id))
    upsert_panel(interaction.guild_id, "absences_channel_id", "absences_message_id", interaction.channel_id, msg.id)
    await interaction.followup.send("✅ Abwesenheit-Panel erstellt.", ephemeral=True)

@panel.command(name="lager", description="Lager Panel in diesem Channel erstellen")
async def panel_lager(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    msg = await interaction.channel.send(embed=inventory_embed(interaction.guild_id), view=InventoryView(interaction.guild_id))
    upsert_panel(interaction.guild_id, "inventory_channel_id", "inventory_message_id", interaction.channel_id, msg.id)
    await interaction.followup.send("✅ Lager-Panel erstellt.", ephemeral=True)

@bot.event
async def on_ready():
    init_db()
    bot.tree.add_command(panel)
    await bot.tree.sync()
    print(f"✅ Bot online als {bot.user}")

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN fehlt.")
bot.run(TOKEN)

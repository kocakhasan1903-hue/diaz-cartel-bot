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

def parse_dt(text: str) -> str:
    dt = date_parser.parse(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def money(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " $"

def clamp(lines: List[str], n=25):
    return lines[:n]

# ---------- Embeds ----------
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
    lines = [f"{r['id']} | {r['name']} | {r['start_at']} | {r['end_at']}" for r in rows]
    body = "Keine Abwesenheiten vorhanden." if not lines else header + "\n".join(clamp(lines, 25))
    e = discord.Embed(title="📌 DÍAZ CARTEL – ABWESENHEITEN", description=f"```{body}```", color=0x1B5E20)
    e.set_footer(text="Buttons: Eintragen | Entfernen")
    return e

def inventory_embed(guild_id: int) -> discord.Embed:
    with db() as con:
        rows = con.execute("SELECT item, qty FROM inventory WHERE guild_id=? ORDER BY item ASC", (guild_id,)).fetchall()
    header = "Item | Menge\n"
    lines = [f"{r['item']} | {r['qty']}" for r in rows]
    body = "Lager ist leer." if not lines else header + "\n".join(clamp(lines, 30))
    e = discord.Embed(title="📦 DÍAZ CARTEL – LAGER", description=f"```{body}```", color=0x263238)
    e.set_footer(text="Buttons: Setzen | Add | Remove")
    return e

# ---------- Modals ----------
class AddSanctionModal(Modal):
    def __init__(self, guild_id: int, msg: discord.Message):
        super().__init__(title="Sanktion hinzufügen")
        self.guild_id = guild_id
        self.msg = msg
        self.name = TextInput(label="Name", max_length=50)
        self.amount = TextInput(label="Betrag (0-100000)", max_length=6)
        self.duration = TextInput(label="Dauer", max_length=30)
        self.reason = TextInput(label="Grund", max_length=200)
        for item in (self.name, self.amount, self.duration, self.reason):
            self.add_item(item)

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
                VALUES (?, ?, ?, ?, ?, 'offen', ?)
            """, (self.guild_id, self.name.value.strip(), amt, self.duration.value.strip(), self.reason.value.strip(), now_iso()))
            con.commit()

        await self.msg.edit(embed=sanctions_embed(self.guild_id), view=SanctionsView(self.guild_id))
        await interaction.response.send_message("✅ Eingetragen.", ephemeral=True)

class SanctionIdModal(Modal):
    def __init__(self, title: str, guild_id: int, msg: discord.Message, mode: str):
        super().__init__(title=title)
        self.guild_id = guild_id
        self.msg = msg
        self.mode = mode
        self.sid = TextInput(label="Sanktions-ID", max_length=10)
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

        await self.msg.edit(embed=sanctions_embed(self.guild_id), view=SanctionsView(self.guild_id))
        await interaction.response.send_message("✅ Aktualisiert.", ephemeral=True)

class AddAbsenceModal(Modal):
    def __init__(self, guild_id: int, msg: discord.Message):
        super().__init__(title="Abwesenheit eintragen")
        self.guild_id = guild_id
        self.msg = msg
        self.name = TextInput(label="Name", max_length=50)
        self.start = TextInput(label="Von (Datum/Zeit)", max_length=40, placeholder="2026-01-28 18:00")
        self.end = TextInput(label="Bis (Datum/Zeit)", max_length=40, placeholder="2026-01-30 18:00")
        self.reason = TextInput(label="Grund", max_length=120)
        for item in (self.name, self.start, self.end, self.reason):
            self.add_item(item)

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

        await self.msg.edit(embed=absences_embed(self.guild_id), view=AbsencesView(self.guild_id))
        await interaction.response.send_message("✅ Eingetragen.", ephemeral=True)

class AbsenceDeleteModal(Modal):
    def __init__(self, guild_id: int, msg: discord.Message):
        super().__init__(title="Abwesenheit entfernen")
        self.guild_id = guild_id
        self.msg = msg
        self.aid = TextInput(label="Abwesenheits-ID", max_length=10)
        self.add_item(self.aid)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            aid = int(self.aid.value.strip())
        except:
            return await interaction.response.send_message("❌ ID muss Zahl sein.", ephemeral=True)

        with db() as con:
            con.execute("DELETE FROM absences WHERE guild_id=? AND id=?", (self.guild_id, aid))
            con.commit()

        await self.msg.edit(embed=absences_embed(self.guild_id), view=AbsencesView(self.guild_id))
        await interaction.response.send_message("✅ Entfernt.", ephemeral=True)

class InventoryModal(Modal):
    def __init__(self, guild_id: int, msg: discord.Message, mode: str):
        super().__init__(title=f"Lager: {mode}")
        self.guild_id = guild_id
        self.msg = msg
        self.mode = mode
        self.item = TextInput(label="Item", max_length=40, placeholder="medikits")
        self.qty = TextInput(label="Menge", max_length=10, placeholder="10")
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

        await self.msg.edit(embed=inventory_embed(self.guild_id), view=InventoryView(self.guild_id))
        await interaction.response.send_message("✅ Lager aktualisiert.", ephemeral=True)

# ---------- Views ----------
class SanctionsView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ Hinzufügen", style=discord.ButtonStyle.primary, custom_id="diaz_s_add")
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddSanctionModal(self.guild_id, interaction.message))

    @discord.ui.button(label="✅ Bezahlt", style=discord.ButtonStyle.success, custom_id="diaz_s_paid")
    async def paid(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SanctionIdModal("Sanktion: Bezahlt", self.guild_id, interaction.message, "paid"))

    @discord.ui.button(label="❌ Offen", style=discord.ButtonStyle.secondary, custom_id="diaz_s_open")
    async def open_(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SanctionIdModal("Sanktion: Offen", self.guild_id, interaction.message, "open"))

    @discord.ui.button(label="🗑️ Löschen", style=discord.ButtonStyle.danger, custom_id="diaz_s_del")
    async def delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SanctionIdModal("Sanktion: Löschen", self.guild_id, interaction.message, "delete"))

class AbsencesView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ Eintragen", style=discord.ButtonStyle.primary, custom_id="diaz_a_add")
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddAbsenceModal(self.guild_id, interaction.message))

    @discord.ui.button(label="🗑️ Entfernen", style=discord.ButtonStyle.danger, custom_id="diaz_a_del")
    async def delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AbsenceDeleteModal(self.guild_id, interaction.message))

class InventoryView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="🧾 Setzen", style=discord.ButtonStyle.secondary, custom_id="diaz_i_set")
    async def set_(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryModal(self.guild_id, interaction.message, "set"))

    @discord.ui.button(label="➕ Add", style=discord.ButtonStyle.success, custom_id="diaz_i_add")
    async def add(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryModal(self.guild_id, interaction.message, "add"))

    @discord.ui.button(label="➖ Remove", style=discord.ButtonStyle.danger, custom_id="diaz_i_rem")
    async def remove(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(InventoryModal(self.guild_id, interaction.message, "remove"))

# ---------- Slash Group ----------
class PanelGroup(app_commands.Group):
    pass

panel = PanelGroup(name="panel", description="Panels erstellen")

@panel.command(name="sanktionen", description="Sanktionen Panel erstellen")
async def panel_sanktionen(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(embed=sanctions_embed(interaction.guild_id), view=SanctionsView(interaction.guild_id))
    await interaction.followup.send("✅ Sanktionen-Panel erstellt.", ephemeral=True)

@panel.command(name="abwesenheit", description="Abwesenheiten Panel erstellen")
async def panel_abwesenheit(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(embed=absences_embed(interaction.guild_id), view=AbsencesView(interaction.guild_id))
    await interaction.followup.send("✅ Abwesenheit-Panel erstellt.", ephemeral=True)

@panel.command(name="lager", description="Lager Panel erstellen")
async def panel_lager(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(embed=inventory_embed(interaction.guild_id), view=InventoryView(interaction.guild_id))
    await interaction.followup.send("✅ Lager-Panel erstellt.", ephemeral=True)

# ---------- Proper setup ----------
@bot.event
async def setup_hook():
    init_db()
    bot.tree.add_command(panel)
    # persistent views (Buttons bleiben nach restart)
    bot.add_view(SanctionsView(0))
    bot.add_view(AbsencesView(0))
    bot.add_view(InventoryView(0))

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot online als {bot.user}")

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN fehlt.")
bot.run(TOKEN)

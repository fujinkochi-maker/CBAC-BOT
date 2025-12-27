import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import get
import os
import random
import json
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Multiple lobbies: {guild_id: {lobby_name: QueueData}}
lobbies = {}

# Player data file
DATA_FILE = "players.json"

# Orange Theme
ORANGE_COLOR = discord.Color.from_rgb(255, 102, 0)  # Main bright orange

# Rank Config with emojis and min ELO
RANK_CONFIG = {
    "Tier 1": {"emoji": "🥇", "min_elo": 1350},
    "Tier 2": {"emoji": "🥈", "min_elo": 1200},
    "Tier 3": {"emoji": "🥉", "min_elo": 1050},
    "Tier 4": {"emoji": "🏅", "min_elo": 900},
    "Tier 5": {"emoji": "🎖️", "min_elo": 750},
    "Tier 6": {"emoji": "🏆", "min_elo": 600},
    "Tier 7": {"emoji": "🔷", "min_elo": 450},
    "Tier 8": {"emoji": "🔶", "min_elo": 300},
    "Tier 9": {"emoji": "🔸", "min_elo": 150},
    "Tier 10": {"emoji": "⚪", "min_elo": 0},
}

class QueueData:
    def __init__(self):
        self.players = []          # list of discord.Member
        self.host = None           # discord.Member
        self.is_open = False
        self.match_started = False

def get_lobbies(guild_id):
    if guild_id not in lobbies:
        lobbies[guild_id] = {}
    return lobbies[guild_id]

# ---------- PLAYER DATA ----------
def load_players():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_players(players):
    with open(DATA_FILE, "w") as f:
        json.dump(players, f, indent=4)

players_data = load_players()

def get_player_stats(user_id):
    str_id = str(user_id)
    if str_id not in players_data:
        players_data[str_id] = {"elo": 0, "wins": 0, "losses": 0}
        save_players(players_data)
    return players_data[str_id]

def update_elo(user_id, change):
    stats = get_player_stats(user_id)
    old_elo = stats["elo"]
    stats["elo"] += change
    if change > 0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1
    save_players(players_data)
    return old_elo  # return old ELO for rank checking

# ---------- RANK SYSTEM ----------
def get_rank_role_name(elo: int) -> str:
    for rank, data in RANK_CONFIG.items():
        if elo >= data["min_elo"]:
            return rank
    return "Tier 10"

def get_progress_to_next(elo: int) -> str:
    current_rank = get_rank_role_name(elo)
    if current_rank == "Tier 1":
        return "**MAX RANK ACHIEVED! 🏆**"

    ranks = list(RANK_CONFIG.keys())
    current_idx = ranks.index(current_rank)
    next_rank = ranks[current_idx - 1]
    next_min = RANK_CONFIG[next_rank]["min_elo"]
    current_min = RANK_CONFIG[current_rank]["min_elo"]

    progress = elo - current_min
    needed = next_min - current_min
    filled = int(progress / needed * 10)
    bar = "█" * filled + "▒" * (10 - filled)
    return f"`{elo}` / `{next_min}`  [{bar}] → **{next_rank}**"

async def update_player_rank(guild: discord.Guild, member: discord.Member, new_elo: int, old_elo: int):
    new_rank = get_rank_role_name(new_elo)
    old_rank = get_rank_role_name(old_elo)
    rank_emoji = RANK_CONFIG[new_rank]["emoji"]

    new_role = get(guild.roles, name=new_rank)
    if not new_role:
        print(f"[WARN] Rank role '{new_rank}' not found!")
        return

    # Remove all tier roles
    tier_roles = [r for r in guild.roles if r.name.startswith("Tier ")]
    await member.remove_roles(*tier_roles, reason="Rank update")

    # Add current rank role
    await member.add_roles(new_role, reason="Rank update")

    # Announcement in #rank-up
    rank_channel = get(guild.text_channels, name="⌏rank-up⌌")
    if not rank_channel:
        return

    if old_elo == 0:
        await rank_channel.send(f"🎉 {member.mention} has entered the ranks as **{new_rank} {rank_emoji}**! (`{new_elo}` ELO)")
    elif new_rank != old_rank:
        if new_elo > old_elo:
            await rank_channel.send(f"⬆️ {member.mention} ranked up to **{new_rank} {rank_emoji}**! (`{old_elo}` → `{new_elo}`)")
        else:
            await rank_channel.send(f"⬇️ {member.mention} dropped to **{new_rank} {rank_emoji}**... (`{old_elo}` → `{new_elo}`)")

# ---------- EMBEDS (ORANGE UI) ----------
def lobby_list_embed(guild_lobbies):
    embed = discord.Embed(title=" ACTIVE LOBBIES", color=ORANGE_COLOR)
    if not guild_lobbies:
        embed.description = "*No active lobbies*"
    for name, queue in guild_lobbies.items():
        status = "✅ Ready" if len(queue.players) >= 10 else f"`{len(queue.players)}/10`"
        host = queue.host.mention if queue.host else "*None*"
        embed.add_field(name=name.upper(), value=f"Host: {host}\nPlayers: {status}", inline=False)
    return embed

def queue_embed(lobby_name, queue):
    embed = discord.Embed(title=f" {lobby_name.upper()}", color=ORANGE_COLOR, timestamp=datetime.now())
    embed.add_field(name="PLAYERS", value=f"`{len(queue.players)}/10`", inline=True)
    embed.add_field(name="HOST", value=queue.host.mention if queue.host else "*None*", inline=True)
    player_list = "\n".join(f"`{i+1}.` {p.mention}" for i, p in enumerate(queue.players)) or "*Empty*"
    embed.add_field(name="QUEUE LIST", value=player_list, inline=False)
    embed.set_footer(text="Use buttons or /join")
    return embed

def profile_embed(member):
    stats = get_player_stats(member.id)
    total = stats["wins"] + stats["losses"]
    winrate = (stats["wins"] / total * 100) if total > 0 else 0
    rank = get_rank_role_name(stats["elo"])
    rank_emoji = RANK_CONFIG[rank]["emoji"]
    progress = get_progress_to_next(stats["elo"])

    embed = discord.Embed(
        title=f"{rank_emoji} {member.display_name.upper()}",
        color=ORANGE_COLOR
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.description = f"**CURRENT RANK:** {rank}\n{progress}"

    embed.add_field(name="ELO", value=f"`{stats['elo']}`", inline=True)
    embed.add_field(name="WINS", value=f"`{stats['wins']}`", inline=True)
    embed.add_field(name="LOSSES", value=f"`{stats['losses']}`", inline=True)
    embed.add_field(name="WINRATE", value=f"`{winrate:.1f}%`", inline=True)
    embed.add_field(name="MATCHES PLAYED", value=f"`{total}`", inline=False)

    embed.set_footer(text="")
    return embed

# ---------- BUTTON VIEW ----------
class LobbyView(discord.ui.View):
    def __init__(self, lobby_name, queue):
        super().__init__(timeout=None)
        self.lobby_name = lobby_name
        self.queue = queue

    async def update(self, interaction):
        await interaction.response.edit_message(embed=queue_embed(self.lobby_name, self.queue), view=self)

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, emoji="➕")
    async def join(self, interaction: discord.Interaction, button):
        queue = get_lobbies(interaction.guild.id).get(self.lobby_name)
        if not queue or not queue.is_open:
            return await interaction.response.send_message("❌ Lobby closed!", ephemeral=True)
        if interaction.user in queue.players:
            return await interaction.response.send_message("❌ Already in!", ephemeral=True)
        if len(queue.players) >= 10:
            return await interaction.response.send_message("❌ Full!", ephemeral=True)
        queue.players.append(interaction.user)
        await self.update(interaction)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger, emoji="➖")
    async def leave(self, interaction: discord.Interaction, button):
        queue = get_lobbies(interaction.guild.id).get(self.lobby_name)
        if not queue or interaction.user not in queue.players:
            return await interaction.response.send_message("❌ Not in lobby!", ephemeral=True)
        queue.players.remove(interaction.user)
        if queue.host == interaction.user:
            queue.host = None
        await self.update(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.grey, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button):
        await self.update(interaction)

    @discord.ui.button(label="Start Match", style=discord.ButtonStyle.success, emoji="🚀")
    async def start(self, interaction: discord.Interaction, button):
        queue = get_lobbies(interaction.guild.id).get(self.lobby_name)
        if not queue:
            return await interaction.response.send_message("❌ Lobby gone!", ephemeral=True)
        if len(queue.players) < 10:
            return await interaction.response.send_message("❌ Need 10 players!", ephemeral=True)
        if not queue.host:
            return await interaction.response.send_message("❌ Need a host!", ephemeral=True)
        if interaction.user != queue.host and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only host or admin!", ephemeral=True)

        queue.match_started = True
        await interaction.response.defer()
        await start_match(interaction, self.lobby_name, queue)

# ---------- START MATCH ----------
async def start_match(interaction, lobby_name, queue):
    random.shuffle(queue.players)
    t_side = queue.players[:5]
    ct_side = queue.players[5:]

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False)
    }
    for member in queue.players:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True)

    category = await interaction.guild.create_category(f"🏆 {lobby_name}", overwrites=overwrites)
    lobby_text = await category.create_text_channel("lobby")
    t_voice = await category.create_voice_channel("🔴 T-SIDE", user_limit=5)
    ct_voice = await category.create_voice_channel("🔵 CT-SIDE", user_limit=5)

    embed = discord.Embed(title=f"🏆 {lobby_name.upper()}", color=ORANGE_COLOR)
    embed.add_field(name="🔴 T-SIDE", value="\n".join(m.mention for m in t_side), inline=True)
    embed.add_field(name="🔵 CT-SIDE", value="\n".join(m.mention for m in ct_side), inline=True)
    embed.add_field(name="👑 Host", value=queue.host.mention, inline=False)
    embed.set_footer(text="Use /reportwin when match ends")

    await lobby_text.send(embed=embed)
    await lobby_text.send(" ".join(m.mention for m in queue.players))

    # Auto move to voice
    for member in t_side:
        if member.voice and member.voice.channel:
            try: await member.move_to(t_voice)
            except: pass
    for member in ct_side:
        if member.voice and member.voice.channel:
            try: await member.move_to(ct_voice)
            except: pass

    await interaction.followup.send(f"✅ Match **{lobby_name}** started! Channels created.")

# ---------- SLASH COMMANDS ----------
@app_commands.command(name="view", description="View all active lobbies")
async def view(interaction: discord.Interaction):
    await interaction.response.send_message(embed=lobby_list_embed(get_lobbies(interaction.guild.id)))

@app_commands.command(name="startlobby", description="Create a lobby (Host role only)")
@app_commands.describe(name="Lobby name")
async def startlobby(interaction: discord.Interaction, name: str):
    host_role = get(interaction.guild.roles, name="Host")
    if host_role not in interaction.user.roles:
        return await interaction.response.send_message("❌ Host role required!", ephemeral=True)

    guild_lobbies = get_lobbies(interaction.guild.id)
    if name in guild_lobbies:
        return await interaction.response.send_message(f"❌ Lobby '{name}' already exists!", ephemeral=True)

    queue = QueueData()
    queue.is_open = True
    queue.host = interaction.user
    guild_lobbies[name] = queue

    view = LobbyView(name, queue)
    await interaction.response.send_message(
        f"🎉 Lobby **{name}** created by {interaction.user.mention}!\nJoin with `/join {name}`",
        embed=queue_embed(name, queue),
        view=view
    )

@app_commands.command(name="join", description="Join a lobby")
@app_commands.describe(name="Lobby name")
async def join(interaction: discord.Interaction, name: str):
    queue = get_lobbies(interaction.guild.id).get(name)
    if not queue or not queue.is_open:
        return await interaction.response.send_message(f"❌ No open lobby '{name}'!", ephemeral=True)
    if interaction.user in queue.players:
        return await interaction.response.send_message("❌ Already in lobby!", ephemeral=True)
    if len(queue.players) >= 10:
        return await interaction.response.send_message("❌ Lobby full!", ephemeral=True)

    queue.players.append(interaction.user)
    await interaction.response.send_message(f"✅ Joined **{name}**!", embed=queue_embed(name, queue), view=LobbyView(name, queue))

@app_commands.command(name="removelobby", description="Delete a lobby (Host role only)")
@app_commands.describe(name="Lobby name")
async def removelobby(interaction: discord.Interaction, name: str):
    host_role = get(interaction.guild.roles, name="Host")
    if host_role not in interaction.user.roles:
        return await interaction.response.send_message("❌ Host role required!", ephemeral=True)

    guild_lobbies = get_lobbies(interaction.guild.id)
    if name not in guild_lobbies:
        return await interaction.response.send_message(f"❌ No lobby '{name}'!", ephemeral=True)

    del guild_lobbies[name]
    await interaction.response.send_message(f"🗑️ Lobby **{name}** removed!")

@app_commands.command(name="reportwin", description="Report winner (Host only)")
@app_commands.describe(name="Lobby name", winner="T or CT")
async def reportwin(interaction: discord.Interaction, name: str, winner: str):
    winner = winner.upper()
    if winner not in ["T", "CT"]:
        return await interaction.response.send_message("❌ Winner must be T or CT!", ephemeral=True)

    queue = get_lobbies(interaction.guild.id).get(name)
    if not queue or not queue.match_started:
        return await interaction.response.send_message("❌ No active match in this lobby!", ephemeral=True)
    if interaction.user != queue.host and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Only host or admin!", ephemeral=True)

    random.shuffle(queue.players)
    t_side = queue.players[:5]
    ct_side = queue.players[5:]
    winning_side = t_side if winner == "T" else ct_side
    losing_side = ct_side if winner == "T" else t_side

    changes = []
    for player in winning_side:
        gain = random.randint(30, 35)
        old_elo = update_elo(player.id, gain)
        changes.append(f"{player.mention} **+{gain}**")
        member = interaction.guild.get_member(player.id)
        if member:
            await update_player_rank(interaction.guild, member, get_player_stats(player.id)["elo"], old_elo)

    for player in losing_side:
        loss = random.randint(25, 30)
        old_elo = update_elo(player.id, -loss)
        changes.append(f"{player.mention} **-{loss}**")
        member = interaction.guild.get_member(player.id)
        if member:
            await update_player_rank(interaction.guild, member, get_player_stats(player.id)["elo"], old_elo)

    embed = discord.Embed(
        title=f"🏆 {name.upper()} — RESULT",
        description=f"**{winner}-SIDE WINS!**",
        color=ORANGE_COLOR,
        timestamp=datetime.now()
    )
    embed.add_field(name="ELO CHANGES", value="\n".join(changes), inline=False)
    embed.set_footer(text="Ranks updated automatically!")

    await interaction.response.send_message(embed=embed)
    del get_lobbies(interaction.guild.id)[name]  # Clean up lobby

@app_commands.command(name="profile", description="View your or another's profile")
@app_commands.describe(player="Player (default: you)")
async def profile(interaction: discord.Interaction, player: discord.Member = None):
    target = player or interaction.user
    await interaction.response.send_message(embed=profile_embed(target))

@app_commands.command(name="leaderboard", description="Top 10 players by ELO")
async def leaderboard(interaction: discord.Interaction):
    sorted_players = sorted(players_data.items(), key=lambda x: x[1]["elo"], reverse=True)[:10]
    embed = discord.Embed(title="🏆 LEADERBOARD", color=ORANGE_COLOR)
    embed.description = "Top grinders"
    for i, (uid, stats) in enumerate(sorted_players, 1):
        member = interaction.guild.get_member(int(uid))
        name = member.display_name.upper() if member else "UNKNOWN"
        rank = get_rank_role_name(stats["elo"])
        rank_emoji = RANK_CONFIG[rank]["emoji"]
        total = stats["wins"] + stats["losses"]
        winrate = (stats["wins"] / total * 100) if total > 0 else 0
        embed.add_field(
            name=f"{i}. {rank_emoji} {name}",
            value=f"ELO: `{stats['elo']}` | WR: `{winrate:.1f}%`",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@app_commands.command(name="end", description="Delete match channels (Admin only)")
async def end_match(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    if not interaction.channel.category or not interaction.channel.category.name.startswith("🏆"):
        return await interaction.response.send_message("❌ Use in a match channel!", ephemeral=True)

    await interaction.response.send_message("🗑️ Deleting in 5 seconds...")
    await asyncio.sleep(5)
    for ch in interaction.channel.category.channels:
        await ch.delete()
    await interaction.channel.category.delete()

# ---------- BOT READY ----------
@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online — ORANGE UI ACTIVATED 🔥")
    await bot.tree.sync()
    print("Commands synced globally!")

# Register commands
bot.tree.add_command(view)
bot.tree.add_command(startlobby)
bot.tree.add_command(join)
bot.tree.add_command(removelobby)
bot.tree.add_command(reportwin)
bot.tree.add_command(profile)
bot.tree.add_command(leaderboard)
bot.tree.add_command(end_match)

bot.run(os.getenv("TOKEN"))
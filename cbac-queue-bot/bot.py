import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import get
import os
import random
import json
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from typing import Optional, List

# Replace with your actual emoji IDs
EMOJIS = {
    "down": "<:down:1455052763456208958>",  # Replace 123... with actual ID
    "up": "<:up:1455052728312266813>",      # Replace 234... with actual ID
    "progress": "<:progress:1455052664877355131>",
    "rank": "<:rank:1455052607599935673>",
    "win": "<:win:1455052572976222229> ",
    "lose": "<:lose:1455052548426825952>",
    "elo": "<:elo:1455052517472866504> ",
    "winrate": "📊",
    "matches": "🎮"
}

load_dotenv()

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Multiple lobbies: {guild_id: {lobby_name: QueueData}}
lobbies = {}

# Store lobby message info: {guild_id: {lobby_name: {"channel_id": x, "message_id": y}}}
lobby_messages = {}

# Map voting data: {guild_id: {lobby_name: {"votes": {user_id: map_name}, "message_id": x}}}
map_votes = {}

# Party data structure: {guild_id: {party_leader_id: PartyData}}
parties = {}

# Substitute requests
substitute_requests = {}

# Match history for corrections and tracking
MATCH_HISTORY_FILE = "match_history.json"

# Player data file with enhanced tracking
DATA_FILE = "players.json"

# Orange Theme
ORANGE_COLOR = discord.Color.from_rgb(255, 102, 0)

# Map Pool
MAP_POOL = ["MIRAGE", "CACHE", "VERTIGO", "INFERNO", "NUKE", "TRAIN"]

# Rank Config with min ELO
RANK_CONFIG = {
    "[ Tier 1 1350+ ]": {"min_elo": 1350},
    "[ Tier 2 | 1200 – 1349 ]": {"min_elo": 1200},
    "[ Tier 3 | 1050 – 1199 ]": {"min_elo": 1050},
    "[ Tier 4 | 900 – 1049 ]": {"min_elo": 900},
    "[ Tier 5 | 750 – 899 ]": {"min_elo": 750},
    "[ Tier 6 | 600 – 749 ]": {"min_elo": 600},
    "[ Tier 7 | 450 – 599 ]": {"min_elo": 450},
    "[ Tier 8 | 300 – 449 ]": {"min_elo": 300},
    "[ Tier 9 |150 – 299 ]": {"min_elo": 150},
    "[ Tier 10 | (0 – 149) ]": {"min_elo": 0},
}

class QueueData:
    def __init__(self):
        self.players = []          # list of discord.Member
        self.host = None           # discord.Member
        self.is_open = False
        self.match_started = False
        self.channel_id = None     # Channel ID where lobby message is
        self.message_id = None     # Message ID of the lobby message
        self.match_category_id = None  # Match category ID
        self.match_lobby_channel_id = None  # Match lobby channel ID
        self.selected_map = None   # Selected map after voting
        self.t_side = []           # Store T-side players
        self.ct_side = []          # Store CT-side players
        self.replacements = {}     # Track replacements: {original_player: replacement_player}

# ==================== ENHANCED PLAYER DATA SYSTEM ====================

class PlayerStats:
    def __init__(self, data=None):
        data = data or {}
        self.elo = data.get("elo", 0)
        self.wins = data.get("wins", 0)
        self.losses = data.get("losses", 0)
        self.recent_matches = data.get("recent_matches", [])  # Store last 10 matches
        self.total_elo_gained = data.get("total_elo_gained", 0)
        self.total_elo_lost = data.get("total_elo_lost", 0)
    
    def to_dict(self):
        return {
            "elo": self.elo,
            "wins": self.wins,
            "losses": self.losses,
            "recent_matches": self.recent_matches[-10:],  # Keep last 10 matches
            "total_elo_gained": self.total_elo_gained,
            "total_elo_lost": self.total_elo_lost
        }
    
    def add_match_result(self, elo_change, opponent_elo=None, map_played=None, result="win"):
        match_data = {
            "timestamp": datetime.now().isoformat(),
            "elo_change": elo_change,
            "new_elo": self.elo,
            "result": result,
            "map": map_played
        }
        if opponent_elo:
            match_data["opponent_elo"] = opponent_elo
        
        self.recent_matches.append(match_data)
        if len(self.recent_matches) > 10:
            self.recent_matches.pop(0)
        
        if elo_change > 0:
            self.total_elo_gained += elo_change
        else:
            self.total_elo_lost += abs(elo_change)

def load_players():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            return {uid: PlayerStats(stats) for uid, stats in data.items()}
    return {}

def save_players(players):
    data = {uid: stats.to_dict() for uid, stats in players.items()}
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

players_data = load_players()

def get_player_stats(user_id):
    str_id = str(user_id)
    if str_id not in players_data:
        players_data[str_id] = PlayerStats()
        save_players(players_data)
    return players_data[str_id]

# ==================== UPDATED ELO SYSTEM ====================

def update_elo_with_protection(user_id, change, match_info=None):
    """Update ELO with protection for 0 ELO players"""
    stats = get_player_stats(user_id)
    
    # If player has 0 ELO and change is negative, don't deduct
    if stats.elo == 0 and change < 0:
        change = 0  # No deduction for 0 ELO players
    
    old_elo = stats.elo
    stats.elo += change
    
    # Update win/loss counts
    if change > 0:
        stats.wins += 1
        result = "win"
    elif change < 0:
        stats.losses += 1
        result = "loss"
    else:
        result = "draw"
    
    # Store match in history
    if match_info:
        stats.add_match_result(change, match_info.get("opponent_elo"), 
                              match_info.get("map"), result)
    
    save_players(players_data)
    return old_elo, change

# Blacklist data structure
BLACKLIST_FILE = "blacklist.json"
blacklist_data = {}

def load_blacklist():
    """Load blacklist from file"""
    global blacklist_data
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, "r") as f:
            blacklist_data = json.load(f)
    return blacklist_data

def save_blacklist():
    """Save blacklist to file"""
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(blacklist_data, f, indent=4)

# Load blacklist on startup
load_blacklist()

# ==================== USER-FRIENDLY PARTY SYSTEM ====================

class PartyData:
    def __init__(self, leader):
        self.leader = leader
        self.members = [leader]
        self.invites = set()  # User IDs who are invited
        self.lobby_name = None  # Which lobby the party is queued for
        self.guild_id = None  # Store guild ID
        self.created_at = datetime.now()
        self.party_code = self.generate_code()  # Simple 4-digit code
    
    def generate_code(self):
        return f"{random.randint(1000, 9999)}"
    
    def is_full(self):
        return len(self.members) >= 5
    
    def add_member(self, member):
        if not self.is_full():
            self.members.append(member)
            return True
        return False
    
    def remove_member(self, member):
        if member in self.members:
            self.members.remove(member)
            return True
        return False

def get_parties(guild_id):
    if guild_id not in parties:
        parties[guild_id] = {}
    return parties[guild_id]

def get_user_party(guild_id, user_id):
    guild_parties = get_parties(guild_id)
    for party_leader, party in guild_parties.items():
        if user_id == party_leader or user_id in [m.id for m in party.members]:
            return party_leader, party
    return None, None

def create_party(guild_id, leader):
    guild_parties = get_parties(guild_id)
    
    # Check if user already in a party
    existing_leader, existing_party = get_user_party(guild_id, leader.id)
    if existing_party:
        return None, "❌ You're already in a party!"
    
    party = PartyData(leader)
    party.guild_id = guild_id
    guild_parties[leader.id] = party
    return party, "✅ Party created!"

def disband_party(guild_id, leader_id):
    if guild_id in parties and leader_id in parties[guild_id]:
        del parties[guild_id][leader_id]
        return True
    return False

def leave_party(guild_id, user_id):
    leader_id, party = get_user_party(guild_id, user_id)
    if not party:
        return False, "❌ You're not in a party"
    
    if user_id == leader_id:
        # Leader leaving - disband party
        disband_party(guild_id, leader_id)
        return True, " Party disbanded (leader left)"
    else:
        # Member leaving
        party.remove_member(party.guild_id.get_member(user_id))
        return True, "👋 Left the party"

def get_online_players_for_invite(guild, party):
    """Get online players who can be invited"""
    online_members = []
    for member in guild.members:
        if (member.bot or 
            member in party.members or 
            member.id in party.invites or
            member.status == discord.Status.offline):
            continue
        
        existing_leader, existing_party = get_user_party(guild.id, member.id)
        if existing_party:
            continue
        
        online_members.append(member)
    
    return online_members

# ==================== ESSENTIAL FUNCTIONS ====================

def is_blacklisted(user_id):
    """Check if a user is blacklisted"""
    str_id = str(user_id)
    if str_id not in blacklist_data:
        return False
    
    blacklist_info = blacklist_data[str_id]
    expires_at = blacklist_info.get("expires_at")
    
    # Check if permanent ban
    if expires_at == "permanent":
        return True
    
    # Check if temporary ban has expired
    if expires_at:
        try:
            expire_time = datetime.fromisoformat(expires_at)
            if datetime.now() > expire_time:
                # Ban expired, remove from blacklist
                remove_from_blacklist(user_id)
                return False
        except:
            pass
    
    return True

def add_to_blacklist(user_id, reason="No reason provided", duration_hours=24, admin_id=None, admin_name="System"):
    """Add user to blacklist"""
    str_id = str(user_id)
    
    if duration_hours == 0:  # Permanent ban
        expires_at = "permanent"
        duration_text = "PERMANENT"
    else:
        expires_at = (datetime.now() + timedelta(hours=duration_hours)).isoformat()
        duration_text = f"{duration_hours} hours"
    
    blacklist_data[str_id] = {
        "user_id": str_id,
        "reason": reason,
        "added_at": datetime.now().isoformat(),
        "expires_at": expires_at,
        "admin_id": str(admin_id) if admin_id else None,
        "admin_name": admin_name,
        "duration_hours": duration_hours
    }
    
    save_blacklist()
    return True

def remove_from_blacklist(user_id):
    """Remove user from blacklist"""
    str_id = str(user_id)
    if str_id in blacklist_data:
        del blacklist_data[str_id]
        save_blacklist()
        return True
    return False

def get_blacklist_info(user_id):
    """Get blacklist information for a user"""
    str_id = str(user_id)
    if str_id not in blacklist_data:
        return None
    return blacklist_data[str_id]

def get_lobbies(guild_id):
    if guild_id not in lobbies:
        lobbies[guild_id] = {}
    return lobbies[guild_id]

def store_lobby_message(guild_id, lobby_name, channel_id, message_id):
    if guild_id not in lobby_messages:
        lobby_messages[guild_id] = {}
    lobby_messages[guild_id][lobby_name] = {
        "channel_id": channel_id,
        "message_id": message_id
    }

def remove_lobby_message(guild_id, lobby_name):
    if guild_id in lobby_messages and lobby_name in lobby_messages[guild_id]:
        del lobby_messages[guild_id][lobby_name]

def get_rank_role_name(elo: int) -> str:
    for rank, data in RANK_CONFIG.items():
        if elo >= data["min_elo"]:
            return rank
    return "[ Tier 10 | (0 – 149) ]"

def get_progress_to_next(elo: int) -> str:
    current_rank = get_rank_role_name(elo)
    if current_rank == "[ Tier 1 1350+ ]":
        return "🏆 MAX RANK ACHIEVED"
    
    ranks = list(RANK_CONFIG.keys())
    current_idx = ranks.index(current_rank)
    next_rank = ranks[current_idx - 1]
    next_min = RANK_CONFIG[next_rank]["min_elo"]
    current_min = RANK_CONFIG[current_rank]["min_elo"]
    
    progress = elo - current_min
    needed = next_min - current_min
    percentage = (progress / needed * 100) if needed > 0 else 0
    
    filled = int(percentage / 10)
    bar = "█" * filled + "░" * (10 - filled)
    
    return f"`{bar}` {percentage:.1f}% to {next_rank}"

async def update_player_rank(guild: discord.Guild, member: discord.Member, new_elo: int, old_elo: int):
    new_rank = get_rank_role_name(new_elo)
    old_rank = get_rank_role_name(old_elo)
    
    new_role = get(guild.roles, name=new_rank)
    if not new_role:
        print(f"[WARN] Rank role '{new_rank}' not found!")
        return
    
    tier_roles = [r for r in guild.roles if r.name.startswith("Tier ")]
    await member.remove_roles(*tier_roles, reason="Rank update")
    await member.add_roles(new_role, reason="Rank update")
    
    rank_channel = get(guild.text_channels, name="⌏rank-up⌌")
    if rank_channel and new_elo > 0:
        if old_elo == 0:
            await rank_channel.send(f"🎉 {member.mention} has entered the ranks as **{new_rank}**! ({new_elo} ELO)")
        elif new_rank != old_rank:
            if new_elo > old_elo:
                await rank_channel.send(f"⬆️ {member.mention} ranked up to **{new_rank}**! ({old_elo} → {new_elo})")
            else:
                await rank_channel.send(f"⬇️ {member.mention} dropped to **{new_rank}**... ({old_elo} → {new_elo})")

# ==================== ENHANCED EMBEDS ====================

def profile_embed(member):
    stats = get_player_stats(member.id)
    total = stats.wins + stats.losses
    winrate = (stats.wins / total * 100) if total > 0 else 0
    rank = get_rank_role_name(stats.elo)
    progress = get_progress_to_next(stats.elo)
    
    embed = discord.Embed(
        title=f" {member.display_name.upper()}'S PROFILE",
        color=ORANGE_COLOR
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    
    embed.add_field(name=f"{EMOJIS['rank']} RANK", value=f"**{rank}**", inline=True)
    embed.add_field(name=f"{EMOJIS['elo']} ELO", value=f"**{stats.elo}**", inline=True)
    embed.add_field(name=f"{EMOJIS['progress']} PROGRESS", value=progress, inline=False)
    
    embed.add_field(name=f"{EMOJIS['win']} WINS", value=stats.wins, inline=True)
    embed.add_field(name=f"{EMOJIS['lose']} LOSSES", value=stats.losses, inline=True)
    embed.add_field(name=f"{EMOJIS['winrate']} WINRATE", value=f"{winrate:.1f}%", inline=True)
    
    embed.add_field(name=f"{EMOJIS['up']} TOTAL ELO GAINED", 
                   value=f"+{stats.total_elo_gained}", inline=True)
    embed.add_field(name=f"{EMOJIS['down']} TOTAL ELO LOST", 
                   value=f"-{stats.total_elo_lost}", inline=True)
    embed.add_field("MATCHES PLAYED", value=total, inline=True)
    
    if stats.recent_matches:
        recent_text = []
        for match in stats.recent_matches[-3:][::-1]:
            change = match["elo_change"]
            result = EMOJIS['up'] if change > 0 else EMOJIS['down'] if change < 0 else "➖"
            map_name = match.get("map", "Unknown")
            recent_text.append(f"{result} **{map_name}**: {change:+d} ELO")
        
        if recent_text:
            embed.add_field(name=" RECENT MATCHES", value="\n".join(recent_text), inline=False)
    
    embed.set_footer(text=f"Party Code: Use /party to create or join")
    return embed

def party_embed(party, show_code=True):
    embed = discord.Embed(
        title="🎉 PARTY",
        color=discord.Color.purple()
    )
    
    embed.add_field(name="👑 LEADER", value=party.leader.mention, inline=True)
    embed.add_field(name="👥 MEMBERS", value=f"{len(party.members)}/5", inline=True)
    
    if show_code:
        embed.add_field(name="🔢 PARTY CODE", value=f"**{party.party_code}**", inline=True)
        embed.set_footer(text=f"Share code: {party.party_code} | Use /partyjoin [code]")
    
    if party.lobby_name:
        embed.add_field(name="🎮 QUEUED FOR", value=f"**{party.lobby_name.upper()}**", inline=False)
    
    member_list = []
    for i, member in enumerate(party.members, 1):
        status = "🟢" if member.status != discord.Status.offline else "⚫"
        role = "👑" if member.id == party.leader.id else "👤"
        member_list.append(f"{i}. {status} {role} {member.mention}")
    
    embed.add_field(name="PARTY MEMBERS", value="\n".join(member_list) if member_list else "No members", inline=False)
    
    if party.invites:
        invites_list = "\n".join(f"• <@{uid}>" for uid in party.invites)
        embed.add_field(name="📨 PENDING INVITES", value=invites_list, inline=False)
    
    return embed

def lobby_list_embed(guild_lobbies):
    embed = discord.Embed(
        title="🎮 ACTIVE LOBBIES",
        color=ORANGE_COLOR
    )
    
    if not guild_lobbies:
        embed.description = "No active lobbies. Create one with `/startlobby`!"
    else:
        for name, queue in guild_lobbies.items():
            status = "✅ READY" if len(queue.players) >= 10 else f"⏳ {len(queue.players)}/10"
            host = queue.host.mention if queue.host else "None"
            embed.add_field(
                name=f"**{name.upper()}**",
                value=f"👑 **Host:** {host}\n👥 **Players:** {status}\n🔓 **Status:** {'Open' if queue.is_open else 'Closed'}",
                inline=False
            )
    
    embed.set_footer(text="Use /join [name] to join a lobby")
    return embed

def queue_embed(lobby_name, queue):
    embed = discord.Embed(
        title=f"🎯 LOBBY: {lobby_name.upper()}",
        description="Click JOIN button below or use `/join` command",
        color=ORANGE_COLOR,
        timestamp=datetime.now()
    )
    
    embed.add_field(name="👥 PLAYERS", value=f"{len(queue.players)}/10", inline=True)
    embed.add_field(name="👑 HOST", value=queue.host.mention if queue.host else "None", inline=True)
    embed.add_field(name="🔓 STATUS", value="✅ Open" if queue.is_open else "❌ Closed", inline=True)
    
    if queue.players:
        player_groups = {}
        solo_players = []
        
        for player in queue.players:
            leader_id, party = get_user_party(queue.host.guild.id, player.id)
            if party and party.lobby_name == lobby_name:
                if leader_id not in player_groups:
                    player_groups[leader_id] = []
                player_groups[leader_id].append(player)
            else:
                solo_players.append(player)
        
        lines = []
        for i, player in enumerate(solo_players, 1):
            elo = get_player_stats(player.id).elo
            lines.append(f"{i}. 👤 {player.mention} ({elo} ELO)")
        
        party_num = len(solo_players) + 1
        for leader_id, members in player_groups.items():
            if members:
                members_str = ", ".join(m.mention for m in members)
                avg_elo = sum(get_player_stats(m.id).elo for m in members) // len(members)
                lines.append(f"{party_num}. 🎉 **PARTY:** {members_str} (Avg: {avg_elo} ELO)")
                party_num += 1
        
        embed.add_field(name="PLAYER LIST", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="PLAYER LIST", value="Empty lobby", inline=False)
    
    embed.set_footer(text="Ready to start when 10 players join!")
    return embed

# ==================== LOBBY VIEW ====================

class LobbyView(discord.ui.View):
    def __init__(self, lobby_name, queue):
        super().__init__(timeout=None)
        self.lobby_name = lobby_name
        self.queue = queue

    async def update(self, interaction):
        await interaction.response.edit_message(embed=queue_embed(self.lobby_name, self.queue), view=self)

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button):
        queue = get_lobbies(interaction.guild.id).get(self.lobby_name)
        if not queue or not queue.is_open:
            return await interaction.response.send_message("Lobby closed!", ephemeral=True)
        if interaction.user in queue.players:
            return await interaction.response.send_message("Already in!", ephemeral=True)
        if len(queue.players) >= 10:
            return await interaction.response.send_message("Full!", ephemeral=True)
        queue.players.append(interaction.user)
        await self.update(interaction)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button):
        queue = get_lobbies(interaction.guild.id).get(self.lobby_name)
        if not queue or interaction.user not in queue.players:
            return await interaction.response.send_message("Not in lobby!", ephemeral=True)
        queue.players.remove(interaction.user)
        if queue.host == interaction.user:
            queue.host = None
        await self.update(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.grey)
    async def refresh(self, interaction: discord.Interaction, button):
        await self.update(interaction)

    @discord.ui.button(label="Start Match", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button):
        queue = get_lobbies(interaction.guild.id).get(self.lobby_name)
        if not queue:
            return await interaction.response.send_message("Lobby gone!", ephemeral=True)
        if len(queue.players) < 10:
            return await interaction.response.send_message("Need 10 players!", ephemeral=True)
        if not queue.host:
            return await interaction.response.send_message("Need a host!", ephemeral=True)
        if interaction.user != queue.host and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only host or admin!", ephemeral=True)

        queue.match_started = True
        await interaction.response.defer()
        await start_match(interaction, self.lobby_name, queue)

# ==================== USER-FRIENDLY PARTY VIEWS ====================

class PartyManageView(discord.ui.View):
    def __init__(self, party_leader_id, party):
        super().__init__(timeout=300)
        self.party_leader_id = party_leader_id
        self.party = party
    
    @discord.ui.button(label="📨 Invite Players", style=discord.ButtonStyle.primary, emoji="👥")
    async def invite_players(self, interaction: discord.Interaction, button):
        if interaction.user.id != self.party_leader_id:
            await interaction.response.send_message("❌ Only the party leader can invite players!", ephemeral=True)
            return
        
        view = PartyInviteView(interaction.guild, self.party)
        await interaction.response.send_message(
            "**👥 Select a player to invite:**\n*Only online players not in parties are shown*",
            view=view,
            ephemeral=True
        )
    
    @discord.ui.button(label="🚪 Leave Party", style=discord.ButtonStyle.danger)
    async def leave_party(self, interaction: discord.Interaction, button):
        success, message = leave_party(interaction.guild.id, interaction.user.id)
        
        if success:
            for child in self.children:
                child.disabled = True
            
            embed = discord.Embed(title="👋 Party Left", description=message, color=discord.Color.red())
            await interaction.response.edit_message(embed=embed, view=self)
            
            leader_id, party = get_user_party(interaction.guild.id, self.party_leader_id)
            if party:
                party_embed_msg = party_embed(party)
                await interaction.followup.send(
                    f"{interaction.user.mention} left the party.",
                    embed=party_embed_msg,
                    ephemeral=False
                )
        else:
            await interaction.response.send_message(message, ephemeral=True)
    
    @discord.ui.button(label="🎮 Queue for Lobby", style=discord.ButtonStyle.success)
    async def queue_for_lobby(self, interaction: discord.Interaction, button):
        if interaction.user.id != self.party_leader_id:
            await interaction.response.send_message("❌ Only the party leader can queue!", ephemeral=True)
            return
        
        guild_lobbies = get_lobbies(interaction.guild.id)
        
        if not guild_lobbies:
            await interaction.response.send_message("❌ No active lobbies! Create one with `/startlobby`", ephemeral=True)
            return
        
        class LobbySelectModal(discord.ui.Modal, title="🎮 Select Lobby"):
            lobby_name = discord.ui.TextInput(
                label="Lobby Name",
                placeholder="Enter lobby name (check with /view)",
                required=True
            )
            
            async def on_submit(self, interaction: discord.Interaction):
                lobby_name = self.lobby_name.value.strip()
                queue = get_lobbies(interaction.guild.id).get(lobby_name)
                
                if not queue or not queue.is_open:
                    await interaction.response.send_message(f"❌ No open lobby named '{lobby_name}'", ephemeral=True)
                    return
                
                if self.view.party.lobby_name:
                    await interaction.response.send_message(
                        f"❌ Party is already queued for {self.view.party.lobby_name}",
                        ephemeral=True
                    )
                    return
                
                can_join = True
                errors = []
                
                for member in self.view.party.members:
                    if member in queue.players:
                        errors.append(f"{member.mention} is already in this lobby")
                        can_join = False
                    elif len(queue.players) + len(self.view.party.members) > 10:
                        errors.append("❌ Lobby doesn't have enough space for the whole party")
                        can_join = False
                        break
                
                if not can_join:
                    error_msg = "\n".join(errors[:3])
                    await interaction.response.send_message(f"**Cannot queue party:**\n{error_msg}", ephemeral=True)
                    return
                
                for member in self.view.party.members:
                    if member not in queue.players:
                        queue.players.append(member)
                
                self.view.party.lobby_name = lobby_name
                
                try:
                    if queue.channel_id and queue.message_id:
                        channel = interaction.guild.get_channel(queue.channel_id)
                        if channel:
                            message = await channel.fetch_message(queue.message_id)
                            await message.edit(embed=queue_embed(lobby_name, queue))
                except:
                    pass
                
                await interaction.response.send_message(
                    f"✅ **Party queued for {lobby_name.upper()}!**\n"
                    f"All {len(self.view.party.members)} members are now in the lobby.\n"
                    f"Use `/leave {lobby_name} party_leave:True` to leave as a group.",
                    ephemeral=False
                )
        
        modal = LobbySelectModal()
        modal.view = self
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="📋 Party Info", style=discord.ButtonStyle.gray)
    async def party_info(self, interaction: discord.Interaction, button):
        embed = party_embed(self.party)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # ADD THIS NEW REFRESH BUTTON
    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.gray)
    async def refresh_party(self, interaction: discord.Interaction, button):
        """Refresh the party view with updated information"""
        
        # Check if party still exists
        leader_id, party = get_user_party(interaction.guild.id, interaction.user.id)
        
        if not party:
            # Party doesn't exist anymore
            for child in self.children:
                child.disabled = True
            
            embed = discord.Embed(
                title="❌ Party Not Found",
                description="This party no longer exists.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        # Check if user is still in the party
        if interaction.user.id not in [m.id for m in party.members]:
            for child in self.children:
                child.disabled = True
            
            embed = discord.Embed(
                title="🚪 Left Party",
                description="You are no longer in this party.",
                color=discord.Color.orange()
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        # Refresh the party embed with updated info
        embed = party_embed(party, show_code=(interaction.user.id == leader_id))
        
        # Update the view
        await interaction.response.edit_message(embed=embed)
        
        # Send confirmation (ephemeral)
        await interaction.followup.send("✅ Party view refreshed!", ephemeral=True)

# ==================== SUBSTITUTE SYSTEM ====================

class SubstituteView(discord.ui.View):
    def __init__(self, lobby_name, player_to_replace):
        super().__init__(timeout=300)
        self.lobby_name = lobby_name
        self.player_to_replace = player_to_replace
    
    @discord.ui.button(label="🔍 Find Replacement", style=discord.ButtonStyle.primary)
    async def find_replacement(self, interaction: discord.Interaction, button):
        queue = get_lobbies(interaction.guild.id).get(self.lobby_name)
        
        if not queue or not queue.match_started:
            await interaction.response.send_message("❌ Match not found!", ephemeral=True)
            return
        
        online_players = []
        for member in interaction.guild.members:
            if (not member.bot and 
                member.status != discord.Status.offline and
                member not in queue.players):
                online_players.append(member)
        
        if not online_players:
            await interaction.response.send_message("❌ No available online players!", ephemeral=True)
            return
        
        options = []
        for player in online_players[:25]:
            elo = get_player_stats(player.id).elo
            options.append(
                discord.SelectOption(
                    label=player.display_name,
                    value=str(player.id),
                    description=f"ELO: {elo}"
                )
            )
        
        class ReplacementSelect(discord.ui.Select):
            async def callback(self, interaction: discord.Interaction):
                replacement_id = int(self.values[0])
                replacement = interaction.guild.get_member(replacement_id)
                
                if not replacement:
                    await interaction.response.send_message("❌ Player not found!", ephemeral=True)
                    return
                
                embed = discord.Embed(
                    title="🔄 Confirm Replacement",
                    description=f"Replace **{self.view.player_to_replace.display_name}** with **{replacement.display_name}**?",
                    color=discord.Color.orange()
                )
                
                confirm_view = discord.ui.View(timeout=60)
                
                async def confirm_callback(interaction: discord.Interaction):
                    success = await replace_player(
                        interaction.guild,
                        self.view.lobby_name,
                        self.view.player_to_replace,
                        replacement
                    )
                    
                    if success:
                        await interaction.response.edit_message(
                            content=f"✅ **Replacement Complete!**\n"
                                   f"{self.view.player_to_replace.mention} → {replacement.mention}",
                            embed=None,
                            view=None
                        )
                    else:
                        await interaction.response.send_message("❌ Replacement failed!", ephemeral=True)
                
                async def cancel_callback(interaction: discord.Interaction):
                    await interaction.response.edit_message(
                        content="❌ Replacement cancelled.",
                        embed=None,
                        view=None
                    )
                
                confirm_button = discord.ui.Button(label="✅ Confirm", style=discord.ButtonStyle.success)
                confirm_button.callback = confirm_callback
                
                cancel_button = discord.ui.Button(label="❌ Cancel", style=discord.ButtonStyle.danger)
                cancel_button.callback = cancel_callback
                
                confirm_view.add_item(confirm_button)
                confirm_view.add_item(cancel_button)
                
                await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=True)
        
        select = ReplacementSelect(placeholder="Select replacement player...", options=options, min_values=1, max_values=1)
        temp_view = discord.ui.View(timeout=60)
        temp_view.add_item(select)
        temp_view.view = self
        
        await interaction.response.send_message("**Select a replacement player:**", view=temp_view, ephemeral=True)
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button):
        await interaction.response.edit_message(content="❌ Replacement request cancelled.", embed=None, view=None)

async def replace_player(guild, lobby_name, old_player, new_player):
    queue = get_lobbies(guild.id).get(lobby_name)
    if not queue or not queue.match_started:
        return False
    
    if new_player in queue.players:
        return False
    
    if old_player in queue.players:
        queue.players.remove(old_player)
        queue.players.append(new_player)
        
        if old_player in queue.t_side:
            queue.t_side.remove(old_player)
            queue.t_side.append(new_player)
            team = "T-SIDE"
        elif old_player in queue.ct_side:
            queue.ct_side.remove(old_player)
            queue.ct_side.append(new_player)
            team = "CT-SIDE"
        else:
            return False
        
        queue.replacements[str(old_player.id)] = str(new_player.id)
        
        match_channel = guild.get_channel(queue.match_lobby_channel_id)
        if match_channel:
            await match_channel.send(
                f"🔄 **PLAYER REPLACEMENT**\n"
                f"**{old_player.mention}** has been replaced by **{new_player.mention}**\n"
                f"**Team:** {team}"
            )
        
        return True
    
    return False

# ==================== MATCH FUNCTIONS ====================

async def start_match(interaction, lobby_name, queue):
    party_groups = {}
    solo_players = []
    
    for player in queue.players:
        leader_id, party = get_user_party(interaction.guild.id, player.id)
        if party and party.lobby_name == lobby_name:
            if leader_id not in party_groups:
                party_groups[leader_id] = {'members': [], 'size': len(party.members)}
            party_groups[leader_id]['members'].append(player)
        else:
            solo_players.append(player)
    
    party_list = list(party_groups.values())
    party_list.sort(key=lambda x: x['size'], reverse=True)
    
    t_side = []
    ct_side = []
    
    def add_to_smaller_team(group):
        nonlocal t_side, ct_side
        if len(t_side) <= len(ct_side):
            t_side.extend(group)
        else:
            ct_side.extend(group)
    
    for party_group in party_list:
        members = party_group['members']
        if len(members) >= 3:
            if len(t_side) + len(members) <= 5:
                t_side.extend(members)
            elif len(ct_side) + len(members) <= 5:
                ct_side.extend(members)
            else:
                half = len(members) // 2
                t_side.extend(members[:half])
                ct_side.extend(members[half:])
        else:
            add_to_smaller_team(members)
    
    random.shuffle(solo_players)
    for player in solo_players:
        add_to_smaller_team([player])
    
    while len(t_side) > 5 or len(ct_side) > 5:
        if len(t_side) > 5:
            move_player = t_side.pop()
            ct_side.append(move_player)
        elif len(ct_side) > 5:
            move_player = ct_side.pop()
            t_side.append(move_player)
    
    if len(t_side) != 5 or len(ct_side) != 5:
        print(f"[WARN] Team balancing failed for {lobby_name}, using random shuffle")
        random.shuffle(queue.players)
        t_side = queue.players[:5]
        ct_side = queue.players[5:]
    
    queue.t_side = t_side
    queue.ct_side = ct_side

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    
    for member in queue.players:
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            connect=True
        )
    
    overwrites[queue.host] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        connect=True
    )
    
    for role in interaction.guild.roles:
        if role.permissions.administrator:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                connect=True
            )

    category = await interaction.guild.create_category(f"MATCH: {lobby_name}", overwrites=overwrites)
    lobby_text = await category.create_text_channel("lobby")
    t_voice = await category.create_voice_channel("T-SIDE", user_limit=5)
    ct_voice = await category.create_voice_channel("CT-SIDE", user_limit=5)

    queue.match_category_id = category.id
    queue.match_lobby_channel_id = lobby_text.id

    lobby_link = f"https://discord.com/channels/{interaction.guild.id}/{lobby_text.id}"
    t_voice_link = f"https://discord.com/channels/{interaction.guild.id}/{t_voice.id}"
    ct_voice_link = f"https://discord.com/channels/{interaction.guild.id}/{ct_voice.id}"

    def format_team_with_parties(team_players):
        lines = []
        team_party_groups = {}
        for player in team_players:
            leader_id, party = get_user_party(interaction.guild.id, player.id)
            if party and party.lobby_name == lobby_name and leader_id in party_groups:
                if leader_id not in team_party_groups:
                    team_party_groups[leader_id] = []
                team_party_groups[leader_id].append(player)
            else:
                team_party_groups[f"solo_{player.id}"] = [player]
        
        for group in team_party_groups.values():
            if len(group) > 1:
                members = ", ".join(m.mention for m in group)
                lines.append(f"👥 {members}")
            else:
                lines.append(f"• {group[0].mention}")
        
        return "\n".join(lines)

    embed = discord.Embed(title=f"MATCH: {lobby_name.upper()}", color=ORANGE_COLOR)
    embed.add_field(name="T-SIDE", value=format_team_with_parties(t_side), inline=True)
    embed.add_field(name="CT-SIDE", value=format_team_with_parties(ct_side), inline=True)
    embed.add_field(name="HOST", value=queue.host.mention, inline=False)
    embed.add_field(name="LOBBY CHANNEL", value=f"[Click to open]({lobby_link})", inline=False)
    embed.add_field(name="VOICE CHANNELS", value=f"**T-Side:** [Join]({t_voice_link})\n**CT-Side:** [Join]({ct_voice_link})", inline=False)
    embed.add_field(name="CHAT PERMISSIONS", value="❌ Only Host can send messages in this channel", inline=False)
    embed.set_footer(text="Use /reportwin when match ends")

    await lobby_text.send(embed=embed)
    await lobby_text.send(" ".join(m.mention for m in queue.players))
    await lobby_text.send(f"**⚠️ CHAT LOCKED:** Only {queue.host.mention} can send messages here. Players can only read.")

    for player in queue.players:
        try:
            dm_embed = discord.Embed(title="Match Started", description=f"Your match {lobby_name} has started", color=ORANGE_COLOR)
            player_team = "T-SIDE" if player in t_side else "CT-SIDE"
            dm_embed.add_field(name="Your Side", value=player_team, inline=False)
            
            leader_id, party = get_user_party(interaction.guild.id, player.id)
            party_mates_on_team = []
            if party and party.lobby_name == lobby_name:
                for member in party.members:
                    if member != player and ((player in t_side and member in t_side) or (player in ct_side and member in ct_side)):
                        party_mates_on_team.append(member)
            
            if party_mates_on_team:
                party_names = ", ".join(m.display_name for m in party_mates_on_team)
                dm_embed.add_field(name="Party Members on Your Team", value=f"🎉 You're with: {party_names}", inline=False)
            
            voice_link = t_voice_link if player in t_side else ct_voice_link
            dm_embed.add_field(name="Match Links", value=f"**Lobby:** [Click here]({lobby_link})\n**Your Voice:** [Join here]({voice_link})", inline=False)
            dm_embed.add_field(name="Host", value=queue.host.mention, inline=False)
            dm_embed.add_field(name="Note", value="The match lobby chat is locked. Only the host can send messages there.", inline=False)
            dm_embed.set_footer(text="Good luck")
            
            await player.send(embed=dm_embed)
        except:
            pass

    await start_map_voting(interaction, lobby_name, queue, lobby_text)

    for member in t_side:
        if member.voice and member.voice.channel:
            try: 
                await member.move_to(t_voice)
            except: 
                pass
    for member in ct_side:
        if member.voice and member.voice.channel:
            try: 
                await member.move_to(ct_voice)
            except: 
                pass

    await interaction.followup.send(f"Match {lobby_name} started! Check your DMs for channel links.")

async def start_map_voting(interaction, lobby_name, queue, lobby_channel):
    guild_id = interaction.guild.id
    if guild_id not in map_votes:
        map_votes[guild_id] = {}
    
    map_votes[guild_id][lobby_name] = {"votes": {}, "message_id": None}
    
    view = MapVoteView(lobby_name, queue.players)
    
    vote_embed = map_vote_embed(lobby_name, map_votes[guild_id][lobby_name])
    vote_message = await lobby_channel.send(embed=vote_embed, view=view)
    
    map_votes[guild_id][lobby_name]["message_id"] = vote_message.id
    view.message = vote_message
    
    await lobby_channel.send(f"🗳️ **MAP VOTING STARTED!**\nAll players please vote for the map you want to play.\nVoting ends in 2 minutes or when all players have voted.")

# ==================== MAP VOTING ====================

def map_vote_embed(lobby_name, votes_data):
    embed = discord.Embed(
        title=f"MAP VOTING: {lobby_name.upper()}",
        description="Vote for the map you want to play!",
        color=ORANGE_COLOR
    )
    
    vote_counts = Counter(votes_data["votes"].values())
    
    for map_name in MAP_POOL:
        count = vote_counts.get(map_name, 0)
        voters = [f"<@{uid}>" for uid, voted_map in votes_data["votes"].items() if voted_map == map_name]
        voter_list = "\n".join(voters) if voters else "No votes"
        embed.add_field(name=f"{map_name} ({count} votes)", value=voter_list, inline=False)
    
    embed.set_footer(text="Voting ends in 2 minutes or when all players have voted")
    return embed

class MapVoteView(discord.ui.View):
    def __init__(self, lobby_name, players):
        super().__init__(timeout=120)
        self.lobby_name = lobby_name
        self.players = players
        self.votes = {}
        self.message = None
        self.vote_ended = False
        
        for map_name in MAP_POOL:
            self.add_item(MapVoteButton(map_name))
    
    async def on_timeout(self):
        if self.vote_ended:
            return
        self.vote_ended = True
        await self.end_voting()
    
    async def end_voting(self):
        guild_id = self.message.guild.id
        vote_counts = Counter(self.votes.values())
        
        if not vote_counts:
            winner = random.choice(MAP_POOL)
        else:
            max_votes = max(vote_counts.values())
            tied_maps = [map_name for map_name, count in vote_counts.items() if count == max_votes]
            winner = random.choice(tied_maps)
        
        queue = get_lobbies(guild_id).get(self.lobby_name)
        if queue:
            queue.selected_map = winner
        
        if guild_id in map_votes and self.lobby_name in map_votes[guild_id]:
            del map_votes[guild_id][self.lobby_name]
        
        result_embed = discord.Embed(
            title=f"MAP VOTING RESULTS: {self.lobby_name.upper()}",
            description=f"**WINNER: {winner}**",
            color=ORANGE_COLOR
        )
        
        for map_name in MAP_POOL:
            count = vote_counts.get(map_name, 0)
            result_embed.add_field(name=map_name, value=f"{count} votes", inline=True)
        
        result_embed.set_footer(text="Map has been selected!")
        
        for child in self.children:
            child.disabled = True
        
        await self.message.edit(embed=result_embed, view=self)
        
        if queue and queue.match_lobby_channel_id:
            try:
                match_channel = self.message.guild.get_channel(queue.match_lobby_channel_id)
                if match_channel:
                    await match_channel.send(f"🗺️ **MAP SELECTED: {winner}**")
                    try:
                        if queue.host:
                            await queue.host.send(f"📋 **Match Configuration for '{self.lobby_name}'**\n**Selected Map:** {winner}\n**Players:** {len(queue.players)}\n\nPlease configure your game server with these settings.")
                    except:
                        pass
            except:
                pass
    
    def check_all_voted(self):
        voted_players = set(self.votes.keys())
        player_ids = {str(p.id) for p in self.players}
        return voted_players == player_ids

class MapVoteButton(discord.ui.Button):
    def __init__(self, map_name):
        super().__init__(label=map_name, style=discord.ButtonStyle.primary)
        self.map_name = map_name
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        
        if str(interaction.user.id) not in {str(p.id) for p in view.players}:
            await interaction.response.send_message("You're not in this match!", ephemeral=True)
            return
        
        view.votes[str(interaction.user.id)] = self.map_name
        guild_id = interaction.guild.id
        if guild_id in map_votes and view.lobby_name in map_votes[guild_id]:
            map_votes[guild_id][view.lobby_name]["votes"][str(interaction.user.id)] = self.map_name
            
            embed = map_vote_embed(view.lobby_name, map_votes[guild_id][view.lobby_name])
            await interaction.response.edit_message(embed=embed)
            
            if view.check_all_voted():
                await view.end_voting()
        else:
            await interaction.response.send_message("Voting session not found!", ephemeral=True)

# ==================== WIN REPORT ====================

async def process_win_report(interaction, lobby_name, queue, winner, t_side, ct_side):
    guild_lobbies = get_lobbies(interaction.guild.id)
    if lobby_name not in guild_lobbies:
        if not interaction.response.is_done():
            await interaction.followup.send("❌ Lobby no longer exists", ephemeral=True)
        return
    
    winning_side = t_side if winner == "T" else ct_side
    losing_side = ct_side if winner == "T" else t_side
    
    elo_gain = random.randint(30, 35)
    elo_loss = random.randint(10, 18)
    
    winner_changes = []
    for player in winning_side:
        opponent_elos = [get_player_stats(p.id).elo for p in losing_side]
        avg_opponent_elo = sum(opponent_elos) // len(opponent_elos) if opponent_elos else 0
        
        old_elo, change = update_elo_with_protection(
            player.id, 
            elo_gain,
            match_info={"opponent_elo": avg_opponent_elo, "map": queue.selected_map}
        )
        
        winner_changes.append(f"✅ {player.mention}: +{change} ELO ({old_elo} → {get_player_stats(player.id).elo})")
        member = interaction.guild.get_member(player.id)
        if member:
            await update_player_rank(interaction.guild, member, get_player_stats(player.id).elo, old_elo)
    
    loser_changes = []
    for player in losing_side:
        opponent_elos = [get_player_stats(p.id).elo for p in winning_side]
        avg_opponent_elo = sum(opponent_elos) // len(opponent_elos) if opponent_elos else 0
        
        old_elo, change = update_elo_with_protection(
            player.id, 
            -elo_loss,
            match_info={"opponent_elo": avg_opponent_elo, "map": queue.selected_map}
        )
        
        if change == 0 and old_elo == 0:
            loser_changes.append(f"🛡️ {player.mention}: No change (Protected at 0 ELO)")
        else:
            loser_changes.append(f"❌ {player.mention}: {change} ELO ({old_elo} → {get_player_stats(player.id).elo})")
        
        member = interaction.guild.get_member(player.id)
        if member:
            await update_player_rank(interaction.guild, member, get_player_stats(player.id).elo, old_elo)
    
    embed = discord.Embed(
        title=f"🏁 MATCH RESULTS: {lobby_name.upper()}",
        description=f"**{winner}-SIDE WINS!**",
        color=ORANGE_COLOR
    )
    
    if queue.selected_map:
        embed.add_field(name="🗺️ MAP", value=queue.selected_map, inline=True)
    
    embed.add_field(name="🎮 WINNERS", value="\n".join(winner_changes), inline=False)
    embed.add_field(name="💀 LOSERS", value="\n".join(loser_changes), inline=False)
    
    if queue.replacements:
        replacements_text = []
        for old_id, new_id in queue.replacements.items():
            old_member = interaction.guild.get_member(int(old_id))
            new_member = interaction.guild.get_member(int(new_id))
            if old_member and new_member:
                replacements_text.append(f"{old_member.mention} → {new_member.mention}")
        
        if replacements_text:
            embed.add_field(name="🔄 REPLACEMENTS", value="\n".join(replacements_text), inline=False)
    
    embed.set_footer(text=f"Reported by {interaction.user.display_name}")
    
    await interaction.followup.send(embed=embed)
    
    cleanup_lobby(interaction.guild.id, lobby_name)

def cleanup_lobby(guild_id, lobby_name):
    if guild_id in lobbies and lobby_name in lobbies[guild_id]:
        del lobbies[guild_id][lobby_name]
    
    if guild_id in lobby_messages and lobby_name in lobby_messages[guild_id]:
        del lobby_messages[guild_id][lobby_name]
    
    for guild_parties in parties.values():
        for party in guild_parties.values():
            if party.lobby_name == lobby_name:
                party.lobby_name = None

# ==================== REPORT WIN VIEW ====================

class ReportWinView(discord.ui.View):
    def __init__(self, lobby_name, queue, t_side, ct_side):
        super().__init__(timeout=60)
        self.lobby_name = lobby_name
        self.queue = queue
        self.t_side = t_side
        self.ct_side = ct_side
        self.processing = False
    
    async def disable_all_buttons(self, interaction: discord.Interaction = None):
        for child in self.children:
            child.disabled = True
        
        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(view=self)
    
    @discord.ui.button(label="Confirm T Win", style=discord.ButtonStyle.danger)
    async def confirm_t(self, interaction: discord.Interaction, button):
        if interaction.user != self.queue.host and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only host or admin!", ephemeral=True)
        
        if self.processing:
            return await interaction.response.send_message("Already processing result!", ephemeral=True)
        
        self.processing = True
        await self.disable_all_buttons(interaction)
        await interaction.delete_original_response()
        await process_win_report(interaction, self.lobby_name, self.queue, "T", self.t_side, self.ct_side)
        self.stop()
    
    @discord.ui.button(label="Confirm CT Win", style=discord.ButtonStyle.primary)
    async def confirm_ct(self, interaction: discord.Interaction, button):
        if interaction.user != self.queue.host and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only host or admin!", ephemeral=True)
        
        if self.processing:
            return await interaction.response.send_message("Already processing result!", ephemeral=True)
        
        self.processing = True
        await self.disable_all_buttons(interaction)
        await interaction.delete_original_response()
        await process_win_report(interaction, self.lobby_name, self.queue, "CT", self.t_side, self.ct_side)
        self.stop()
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button):
        if interaction.user != self.queue.host and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only host or admin!", ephemeral=True)
        
        await self.disable_all_buttons(interaction)
        await interaction.edit_message(content="Report cancelled.", embed=None, view=None)
        self.stop()
    
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

# ==================== ALL SLASH COMMANDS ====================

@app_commands.command(name="view", description="View all active lobbies")
async def view(interaction: discord.Interaction):
    await interaction.response.send_message(embed=lobby_list_embed(get_lobbies(interaction.guild.id)))

    @discord.ui.button(label="Start Match", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button):
        queue = get_lobbies(interaction.guild.id).get(self.lobby_name)
        if not queue:
            return await interaction.response.send_message("Lobby gone!", ephemeral=True)
        
        # Check if enough players
        if len(queue.players) < 10:
            return await interaction.response.send_message("❌ Need 10 players to start match!", ephemeral=True)
        
        # Check permissions - Only host or admin
        if interaction.user != queue.host and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only host or admin can start match!", ephemeral=True)

        queue.match_started = True
        await interaction.response.defer()
        await start_match(interaction, self.lobby_name, queue)

@app_commands.command(name="startlobby", description="Create a lobby (Host role only)")
@app_commands.describe(name="Lobby name")
async def startlobby(interaction: discord.Interaction, name: str):
    # BLACKLIST CHECK
    if is_blacklisted(interaction.user.id):
        return await interaction.response.send_message(
            "❌ You are blacklisted and cannot create lobbies!\n"
            "Use `/blacklistinfo` to see details.",
            ephemeral=True
        )
    
    host_role = get(interaction.guild.roles, name="Host")
    if host_role not in interaction.user.roles:
        return await interaction.response.send_message("Host role required", ephemeral=True)

    guild_lobbies = get_lobbies(interaction.guild.id)
    if name in guild_lobbies:
        return await interaction.response.send_message(f"Lobby '{name}' already exists", ephemeral=True)

    queue = QueueData()
    queue.is_open = True
    queue.host = interaction.user
    guild_lobbies[name] = queue

    players_role = get(interaction.guild.roles, name="[ Players ]")
    ping_text = f"{players_role.mention} " if players_role else ""

    view_obj = LobbyView(name, queue)
    sent_message = await interaction.response.send_message(
        f"{ping_text}Lobby {name} created by {interaction.user.mention}\nJoin with /join {name} or use buttons below",
        embed=queue_embed(name, queue),
        view=view_obj
    )
    
    queue.channel_id = interaction.channel.id
    queue.message_id = sent_message.id
    store_lobby_message(interaction.guild.id, name, interaction.channel.id, sent_message.id)

@app_commands.command(name="join", description="Join a lobby")
@app_commands.describe(name="Lobby name", as_party="Join with your whole party (leader only)")
async def join(interaction: discord.Interaction, name: str, as_party: bool = False):
    # BLACKLIST CHECK
    if is_blacklisted(interaction.user.id):
        info = get_blacklist_info(interaction.user.id)
        reason = info.get("reason", "No reason provided") if info else "No reason provided"
        
        embed = discord.Embed(
            title="🚫 YOU ARE BLACKLISTED",
            description="You cannot join lobbies while blacklisted",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason)
        
        expires_at = info.get("expires_at") if info else None
        if expires_at == "permanent":
            embed.add_field(name="Duration", value="PERMANENT")
        elif expires_at:
            try:
                expire_time = datetime.fromisoformat(expires_at)
                time_left = expire_time - datetime.now()
                if time_left.total_seconds() > 0:
                    hours_left = int(time_left.total_seconds() / 3600)
                    minutes_left = int((time_left.total_seconds() % 3600) / 60)
                    embed.add_field(name="Time Left", value=f"{hours_left}h {minutes_left}m")
            except:
                pass
        
        embed.set_footer(text="Contact admins for appeal")
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    queue = get_lobbies(interaction.guild.id).get(name)
    if not queue or not queue.is_open:
        return await interaction.response.send_message(f"No open lobby '{name}'", ephemeral=True)
    
    if as_party:
        leader_id, party = get_user_party(interaction.guild.id, interaction.user.id)
        
        if not party:
            return await interaction.response.send_message("You're not in a party!", ephemeral=True)
        
        if interaction.user.id != leader_id:
            return await interaction.response.send_message("Only the party leader can queue the whole party!", ephemeral=True)
        
        if party.lobby_name:
            return await interaction.response.send_message(f"Party is already queued for {party.lobby_name}. Leave that queue first.", ephemeral=True)
        
        can_join_all = True
        errors = []
        
        for member in party.members:
            if member in queue.players:
                errors.append(f"{member.mention} is already in this lobby")
                can_join_all = False
            elif len(queue.players) + len(party.members) > 10:
                errors.append("Not enough space for the whole party")
                can_join_all = False
                break
        
        if not can_join_all:
            error_msg = "\n".join(errors)
            return await interaction.response.send_message(f"Cannot queue party:\n{error_msg}", ephemeral=True)
        
        for member in party.members:
            if member not in queue.players:
                queue.players.append(member)
        
        party.lobby_name = name
        
        await interaction.response.send_message(
            f"✅ Party of {len(party.members)} players queued for **{name}**!\n"
            f"All party members have been added to the lobby.",
            embed=queue_embed(name, queue),
            view=LobbyView(name, queue)
        )
    else:
        if interaction.user in queue.players:
            return await interaction.response.send_message("Already in lobby", ephemeral=True)
        if len(queue.players) >= 10:
            return await interaction.response.send_message("Lobby full", ephemeral=True)
        
        queue.players.append(interaction.user)
        await interaction.response.send_message(f"Joined {name}", embed=queue_embed(name, queue), view=LobbyView(name, queue))

@app_commands.command(name="join", description="Join a lobby")
@app_commands.describe(name="Lobby name", as_party="Join with your whole party (leader only)")
async def join(interaction: discord.Interaction, name: str, as_party: bool = False):
    # BLACKLIST CHECK
    if is_blacklisted(interaction.user.id):
        info = get_blacklist_info(interaction.user.id)
        reason = info.get("reason", "No reason provided") if info else "No reason provided"
        
        embed = discord.Embed(
            title="🚫 YOU ARE BLACKLISTED",
            description="You cannot join lobbies while blacklisted",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason)
        
        expires_at = info.get("expires_at") if info else None
        if expires_at == "permanent":
            embed.add_field(name="Duration", value="PERMANENT")
        elif expires_at:
            try:
                expire_time = datetime.fromisoformat(expires_at)
                time_left = expire_time - datetime.now()
                if time_left.total_seconds() > 0:
                    hours_left = int(time_left.total_seconds() / 3600)
                    minutes_left = int((time_left.total_seconds() % 3600) / 60)
                    embed.add_field(name="Time Left", value=f"{hours_left}h {minutes_left}m")
            except:
                pass
        
        embed.set_footer(text="Contact admins for appeal")
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    queue = get_lobbies(interaction.guild.id).get(name)
    if not queue or not queue.is_open:
        return await interaction.response.send_message(f"No open lobby '{name}'", ephemeral=True)
    
    if as_party:
        leader_id, party = get_user_party(interaction.guild.id, interaction.user.id)
        
        if not party:
            return await interaction.response.send_message("You're not in a party!", ephemeral=True)
        
        if interaction.user.id != leader_id:
            return await interaction.response.send_message("Only the party leader can queue the whole party!", ephemeral=True)
        
        if party.lobby_name:
            return await interaction.response.send_message(f"Party is already queued for {party.lobby_name}. Leave that queue first.", ephemeral=True)
        
        can_join_all = True
        errors = []
        
        for member in party.members:
            if member in queue.players:
                errors.append(f"{member.mention} is already in this lobby")
                can_join_all = False
            elif len(queue.players) + len(party.members) > 10:
                errors.append("Not enough space for the whole party")
                can_join_all = False
                break
        
        if not can_join_all:
            error_msg = "\n".join(errors)
            return await interaction.response.send_message(f"Cannot queue party:\n{error_msg}", ephemeral=True)
        
        for member in party.members:
            if member not in queue.players:
                queue.players.append(member)
        
        party.lobby_name = name
        
        await interaction.response.send_message(
            f"✅ Party of {len(party.members)} players queued for **{name}**!\n"
            f"All party members have been added to the lobby.",
            embed=queue_embed(name, queue),
            view=LobbyView(name, queue)
        )
    else:
        if interaction.user in queue.players:
            return await interaction.response.send_message("Already in lobby", ephemeral=True)
        if len(queue.players) >= 10:
            return await interaction.response.send_message("Lobby full", ephemeral=True)
        
        queue.players.append(interaction.user)
        await interaction.response.send_message(f"Joined {name}", embed=queue_embed(name, queue), view=LobbyView(name, queue))

@app_commands.command(name="leave", description="Leave a lobby")
@app_commands.describe(name="Lobby name", party_leave="Leave with your whole party (leader only)")
async def leave(interaction: discord.Interaction, name: str, party_leave: bool = False):
    queue = get_lobbies(interaction.guild.id).get(name)
    if not queue or interaction.user not in queue.players:
        return await interaction.response.send_message("Not in lobby!", ephemeral=True)
    
    if party_leave:
        leader_id, party = get_user_party(interaction.guild.id, interaction.user.id)
        
        if not party:
            return await interaction.response.send_message("You're not in a party!", ephemeral=True)
        
        if interaction.user.id != leader_id:
            return await interaction.response.send_message("Only the party leader can unqueue the whole party!", ephemeral=True)
        
        if party.lobby_name != name:
            return await interaction.response.send_message(f"Your party is not queued for {name}!", ephemeral=True)
        
        removed_count = 0
        for member in party.members:
            if member in queue.players:
                queue.players.remove(member)
                removed_count += 1
        
        party.lobby_name = None
        
        await interaction.response.send_message(
            f"✅ Party of {removed_count} players left **{name}**!\n"
            f"All party members have been removed from the lobby.",
            embed=queue_embed(name, queue),
            view=LobbyView(name, queue)
        )
    else:
        queue.players.remove(interaction.user)
        if queue.host == interaction.user:
            queue.host = None
        
        leader_id, party = get_user_party(interaction.guild.id, interaction.user.id)
        if party and party.lobby_name == name:
            party_still_in_queue = any(m in queue.players for m in party.members if m != interaction.user)
            if not party_still_in_queue:
                party.lobby_name = None
        
        await interaction.response.send_message(f"Left {name}", embed=queue_embed(name, queue), view=LobbyView(name, queue))

@app_commands.command(name="removelobby", description="Delete a lobby (Admin only after match starts)")
@app_commands.describe(name="Lobby name")
async def removelobby(interaction: discord.Interaction, name: str):
    host_role = get(interaction.guild.roles, name="Host")
    queue = get_lobbies(interaction.guild.id).get(name)
    
    if not queue:
        return await interaction.response.send_message(f"No lobby '{name}'", ephemeral=True)
    
    # If match has started, only admin can remove
    if queue.match_started:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Match has started! Only admins can remove started matches.",
                ephemeral=True
            )
    else:
        # If match hasn't started, host or admin can remove
        if host_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Host role or Admin required", ephemeral=True)
    
    # Try to delete the lobby message if it exists
    try:
        if queue.channel_id and queue.message_id:
            channel = interaction.guild.get_channel(queue.channel_id)
            if channel:
                message = await channel.fetch_message(queue.message_id)
                await message.delete()
    except:
        pass  # If message already deleted or not found, continue
    
    # Also try from backup dictionary
    try:
        if interaction.guild.id in lobby_messages and name in lobby_messages[interaction.guild.id]:
            msg_info = lobby_messages[interaction.guild.id][name]
            channel = interaction.guild.get_channel(msg_info["channel_id"])
            if channel:
                message = await channel.fetch_message(msg_info["message_id"])
                await message.delete()
            remove_lobby_message(interaction.guild.id, name)
    except:
        pass

    # Clear party lobby tracking for this lobby
    for player in queue.players:
        leader_id, party = get_user_party(interaction.guild.id, player.id)
        if party and party.lobby_name == name:
            party.lobby_name = None

    # Remove lobby from data
    del get_lobbies(interaction.guild.id)[name]
    
    if queue.match_started:
        await interaction.response.send_message(f"✅ Admin removed started lobby: {name}")
    else:
        await interaction.response.send_message(f"Lobby {name} removed")

@app_commands.command(name="reportwin", description="Report match winner (Admin only)")
@app_commands.describe(name="Lobby name", winner="T or CT")
async def reportwin(interaction: discord.Interaction, name: str, winner: str):
    winner = winner.upper()
    if winner not in ["T", "CT"]:
        return await interaction.response.send_message("Winner must be T or CT", ephemeral=True)

    # ADMIN CHECK - ADD THIS
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only command!", ephemeral=True)

    queue = get_lobbies(interaction.guild.id).get(name)
    if not queue or not queue.match_started:
        return await interaction.response.send_message("No active match in this lobby", ephemeral=True)
    
    # Removed host check since only admins can use it now
    # if interaction.user != queue.host and not interaction.user.guild_permissions.administrator:
    #     return await interaction.response.send_message("Only host or admin", ephemeral=True)

    # Use stored teams
    t_side = queue.t_side
    ct_side = queue.ct_side
    
    # Check if teams are stored
    if not t_side or not ct_side or len(t_side) == 0 or len(ct_side) == 0:
        # Fallback to reshuffle
        random.shuffle(queue.players)
        t_side = queue.players[:5]
        ct_side = queue.players[5:]
        queue.t_side = t_side
        queue.ct_side = ct_side
    
    # Create confirmation embed
    embed = discord.Embed(
        title="ADMIN: CONFIRM MATCH RESULT",
        description=f"Are you sure {winner}-SIDE won?\n\nThis cannot be undone",
        color=discord.Color.orange()
    )
    
    if queue.selected_map:
        embed.add_field(name="MAP PLAYED", value=queue.selected_map, inline=False)
    
    embed.add_field(name="T-SIDE", value="\n".join(m.mention for m in t_side), inline=True)
    embed.add_field(name="CT-SIDE", value="\n".join(m.mention for m in ct_side), inline=True)
    embed.set_footer(text="Admin only - Click the correct winner button below")
    
    view = ReportWinView(name, queue, t_side, ct_side)
    await interaction.response.send_message(embed=embed, view=view)

@app_commands.command(name="addelo", description="Add ELO to a player (Admin only)")
@app_commands.describe(player="Player to add ELO to", amount="Amount of ELO to add")
async def addelo(interaction: discord.Interaction, player: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only", ephemeral=True)
    
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive", ephemeral=True)
    
    try:
        stats = get_player_stats(player.id)
        old_elo = stats.elo
        
        update_elo_with_protection(player.id, amount)
        new_stats = get_player_stats(player.id)
        new_elo = new_stats.elo
        
        await update_player_rank(interaction.guild, player, new_elo, old_elo)
        
        await interaction.response.send_message(
            f"✅ Added {amount} ELO to {player.mention}\n"
            f"**Old ELO:** {old_elo} → **New ELO:** {new_elo} (+{amount})"
        )
    except Exception as e:
        print(f"Error in addelo: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

@app_commands.command(name="removeelo", description="Remove ELO from a player (Admin only)")
@app_commands.describe(player="Player to remove ELO from", amount="Amount of ELO to remove")
async def removeelo(interaction: discord.Interaction, player: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only", ephemeral=True)
    
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive", ephemeral=True)
    
    try:
        stats = get_player_stats(player.id)
        old_elo = stats.elo
        
        update_elo_with_protection(player.id, -amount)
        new_stats = get_player_stats(player.id)
        new_elo = new_stats.elo
        
        await update_player_rank(interaction.guild, player, new_elo, old_elo)
        
        await interaction.response.send_message(
            f"✅ Removed {amount} ELO from {player.mention}\n"
            f"**Old ELO:** {old_elo} → **New ELO:** {new_elo} (-{amount})"
        )
    except Exception as e:
        print(f"Error in removeelo: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

def load_match_history():
    if os.path.exists(MATCH_HISTORY_FILE):
        with open(MATCH_HISTORY_FILE, "r") as f:
            return json.load(f)
    return {}

def save_match_history(history):
    with open(MATCH_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

@app_commands.command(name="correctwin", description="Correct a wrongly reported match (Admin only)")
@app_commands.describe(lobby_name="Original lobby name", correct_winner="Correct winner: T or CT")
async def correctwin(interaction: discord.Interaction, lobby_name: str, correct_winner: str):
    correct_winner = correct_winner.upper()
    
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only", ephemeral=True)
    
    if correct_winner not in ["T", "CT"]:
        return await interaction.response.send_message("Winner must be T or CT", ephemeral=True)
    
    match_history = load_match_history()
    match_key = None
    for key, match in match_history.items():
        if match.get("lobby_name") == lobby_name:
            match_key = key
            break
    
    if not match_key:
        return await interaction.response.send_message(f"No match history found for '{lobby_name}'", ephemeral=True)
    
    match_data = match_history[match_key]
    if match_data.get("winner") == correct_winner:
        return await interaction.response.send_message(f"Match already reported as {correct_winner}-SIDE win", ephemeral=True)
    
    changes = []
    
    for player_id in match_data.get("winning_side", []):
        player = interaction.guild.get_member(int(player_id))
        if player:
            wrong_gain = match_data.get("elo_gain", 32)
            old_elo = update_elo_with_protection(player.id, -wrong_gain)
            changes.append(f"{player.mention} -{wrong_gain} (wrong gain removed)")
            await update_player_rank(interaction.guild, player, get_player_stats(player.id).elo, old_elo)
    
    for player_id in match_data.get("losing_side", []):
        player = interaction.guild.get_member(int(player_id))
        if player:
            wrong_loss = match_data.get("elo_loss", 14)
            old_elo = update_elo_with_protection(player.id, wrong_loss)
            changes.append(f"{player.mention} +{wrong_loss} (wrong loss refunded)")
            await update_player_rank(interaction.guild, player, get_player_stats(player.id).elo, old_elo)
    
    new_winning_side = match_data["losing_side"]
    new_losing_side = match_data["winning_side"]
    
    for player_id in new_winning_side:
        player = interaction.guild.get_member(int(player_id))
        if player:
            gain = match_data.get("elo_gain", 32)
            old_elo = update_elo_with_protection(player.id, gain)
            changes.append(f"{player.mention} +{gain} (correct win)")
            await update_player_rank(interaction.guild, player, get_player_stats(player.id).elo, old_elo)
    
    for player_id in new_losing_side:
        player = interaction.guild.get_member(int(player_id))
        if player:
            loss = match_data.get("elo_loss", 14)
            old_elo = update_elo_with_protection(player.id, -loss)
            changes.append(f"{player.mention} -{loss} (correct loss)")
            await update_player_rank(interaction.guild, player, get_player_stats(player.id).elo, old_elo)
    
    embed = discord.Embed(
        title=f"Match Correction: {lobby_name.upper()}",
        description=f"Corrected to: {correct_winner}-SIDE WINS\nPrevious: {match_data['winner']}-SIDE",
        color=discord.Color.blue()
    )
    
    if match_data.get("selected_map"):
        embed.add_field(name="MAP PLAYED", value=match_data["selected_map"], inline=False)
    
    embed.add_field(name="ELO Adjustments", value="\n".join(changes), inline=False)
    embed.set_footer(text="Match result corrected by admin")
    
    del match_history[match_key]
    save_match_history(match_history)
    await interaction.response.send_message(embed=embed)

@app_commands.command(name="profile", description="View your or another's profile")
@app_commands.describe(player="Player (default: you)")
async def profile(interaction: discord.Interaction, player: discord.Member = None):
    target = player or interaction.user
    await interaction.response.send_message(embed=profile_embed(target))

@app_commands.command(name="leaderboard", description="Top 10 players by ELO")
async def leaderboard(interaction: discord.Interaction):
    sorted_players = sorted(players_data.items(), key=lambda x: x[1].elo, reverse=True)[:10]
    embed = discord.Embed(title="LEADERBOARD", color=ORANGE_COLOR)
    embed.description = "Top players by ELO"
    for i, (uid, stats) in enumerate(sorted_players, 1):
        member = interaction.guild.get_member(int(uid))
        name = member.display_name.upper() if member else "UNKNOWN"
        rank = get_rank_role_name(stats.elo)
        total = stats.wins + stats.losses
        winrate = (stats.wins / total * 100) if total > 0 else 0
        embed.add_field(
            name=f"{i}. {name}",
            value=f"ELO: {stats.elo} | WR: {winrate:.1f}% | Rank: {rank}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@app_commands.command(name="end", description="Delete match channels (Admin only)")
async def end_match(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only", ephemeral=True)
    if not interaction.channel.category or not interaction.channel.category.name.startswith("MATCH:"):
        return await interaction.response.send_message("Use in a match channel", ephemeral=True)

    await interaction.response.send_message("Deleting in 5 seconds...")
    await asyncio.sleep(5)
    for ch in interaction.channel.category.channels:
        await ch.delete()
    await interaction.channel.category.delete()

@app_commands.command(name="party", description="Create or manage your party")
async def party_command(interaction: discord.Interaction):
    leader_id, existing_party = get_user_party(interaction.guild.id, interaction.user.id)
    
    if existing_party:
        embed = party_embed(existing_party)
        view = PartyManageView(leader_id, existing_party)
        await interaction.response.send_message(
            "**🎉 YOUR PARTY**\n"
            "**Commands:**\n"
            "• **Invite Players** - Invite online players\n"
            "• **Queue for Lobby** - Join a lobby as a party\n"
            "• **Refresh** - Update party status\n"
            "• **Party Info** - View party details\n"
            "• **Leave Party** - Leave the party",
            embed=embed, 
            view=view
        )
    else:
        party, message = create_party(interaction.guild.id, interaction.user)
        if party:
            embed = party_embed(party)
            view = PartyManageView(interaction.user.id, party)
            await interaction.response.send_message(
                f"✅ **PARTY CREATED!**\n"
                f"**Party Code:** `{party.party_code}`\n"
                f"Share this code with friends to join easily!\n\n"
                f"**Quick Commands:**\n"
                f"• `/partyjoin {party.party_code}` - Join with code\n"
                f"• `/partyleave` - Leave the party\n"
                f"• `/partyinfo` - View party details\n"
                f"• **Refresh button** - Update party status",
                embed=embed,
                view=view
            )
        else:
            await interaction.response.send_message(message, ephemeral=True)

@app_commands.command(name="partyjoin", description="Join a party using code")
@app_commands.describe(party_code="4-digit party code")
async def partyjoin(interaction: discord.Interaction, party_code: str):
    party_code = party_code.strip()
    target_party = None
    target_leader = None
    
    for leader_id, party in get_parties(interaction.guild.id).items():
        if party.party_code == party_code:
            target_party = party
            target_leader = leader_id
            break
    
    if not target_party:
        await interaction.response.send_message("❌ Invalid party code!", ephemeral=True)
        return
    
    existing_leader, existing_party = get_user_party(interaction.guild.id, interaction.user.id)
    if existing_party:
        await interaction.response.send_message("❌ You're already in a party!", ephemeral=True)
        return
    
    if target_party.is_full():
        await interaction.response.send_message("❌ Party is full! (Max 5 players)", ephemeral=True)
        return
    
    if target_party.add_member(interaction.user):
        embed = party_embed(target_party)
        await interaction.response.send_message(f"✅ **Joined party!**\nYou're now in {target_party.leader.mention}'s party.", embed=embed)
        for member in target_party.members:
            if member.id != interaction.user.id:
                try:
                    await member.send(f"🎉 {interaction.user.mention} joined your party!")
                except:
                    pass
    else:
        await interaction.response.send_message("❌ Failed to join party!", ephemeral=True)

@app_commands.command(name="partyleave", description="Leave your current party")
async def partyleave(interaction: discord.Interaction):
    success, message = leave_party(interaction.guild.id, interaction.user.id)
    if success:
        embed = discord.Embed(title="👋 Party Left", description=message, color=discord.Color.orange())
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(message, ephemeral=True)

@app_commands.command(name="partyinfo", description="View party information")
@app_commands.describe(member="Check another player's party")
async def partyinfo(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    leader_id, party = get_user_party(interaction.guild.id, target.id)
    
    if party:
        embed = party_embed(party, show_code=(interaction.user.id == leader_id))
        if interaction.user.id == leader_id:
            view = PartyManageView(leader_id, party)
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed)
    else:
        if member:
            await interaction.response.send_message(f"{member.mention} is not in a party.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You're not in a party!\nUse `/party` to create one or `/partyjoin [code]` to join.", ephemeral=True)

@app_commands.command(name="kickplayer", description="Kick a player from your lobby (Host only)")
@app_commands.describe(player="Player to kick", lobby_name="Lobby name (optional if you're host)")
async def kickplayer(interaction: discord.Interaction, player: discord.Member, lobby_name: str = None):
    """Kick a player from a lobby - Host only"""
    
    # Find the lobby
    target_lobby_name = None
    target_queue = None
    
    if lobby_name:
        # Use specified lobby name
        target_queue = get_lobbies(interaction.guild.id).get(lobby_name)
        if not target_queue:
            return await interaction.response.send_message(f"No lobby named '{lobby_name}' found!", ephemeral=True)
        target_lobby_name = lobby_name
    else:
        # Find lobbies where user is host
        for name, queue in get_lobbies(interaction.guild.id).items():
            if queue.host == interaction.user:
                target_queue = queue
                target_lobby_name = name
                break
        
        if not target_queue:
            return await interaction.response.send_message(
                "❌ You need to specify a lobby name or be hosting a lobby!\n"
                "Usage: `/kickplayer player: @Player lobby_name: LobbyName`",
                ephemeral=True
            )
    
    # Check permissions
    if interaction.user != target_queue.host and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Only the host or admin can kick players!", ephemeral=True)
    
    # Check if player is in the lobby
    if player not in target_queue.players:
        return await interaction.response.send_message(f"❌ {player.mention} is not in this lobby!", ephemeral=True)
    
    # Can't kick yourself
    if player == interaction.user:
        return await interaction.response.send_message("❌ You can't kick yourself! Use `/leave` instead.", ephemeral=True)
    
    # Can't kick the host (unless admin)
    if player == target_queue.host and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ You can't kick the host!", ephemeral=True)
    
    # Remove player from lobby
    target_queue.players.remove(player)
    
    # Check if player was in a party
    leader_id, party = get_user_party(interaction.guild.id, player.id)
    if party and party.lobby_name == target_lobby_name:
        # Check if this was the last party member in the lobby
        party_still_in_lobby = any(m in target_queue.players for m in party.members)
        if not party_still_in_lobby:
            party.lobby_name = None
    
    # Update lobby message
    try:
        if target_queue.channel_id and target_queue.message_id:
            channel = interaction.guild.get_channel(target_queue.channel_id)
            if channel:
                message = await channel.fetch_message(target_queue.message_id)
                await message.edit(embed=queue_embed(target_lobby_name, target_queue))
    except:
        pass
    
    # Send confirmation
    await interaction.response.send_message(
        f"✅ **{player.display_name}** has been kicked from **{target_lobby_name}**!\n"
        f"Remaining players: {len(target_queue.players)}/10",
        ephemeral=False
    )
    
    # Send DM to kicked player
    try:
        kick_embed = discord.Embed(
            title="🚫 Kicked from Lobby",
            description=f"You have been kicked from lobby **{target_lobby_name}**",
            color=discord.Color.red()
        )
        kick_embed.add_field(name="Kicked by", value=interaction.user.mention, inline=True)
        kick_embed.add_field(name="Lobby", value=target_lobby_name.upper(), inline=True)
        kick_embed.add_field(name="Reason", value="Host decision", inline=False)
        kick_embed.set_footer(text="You can join other lobbies with /view")
        
        await player.send(embed=kick_embed)
    except discord.Forbidden:
        pass  # User has DMs disabled
    
    # Notify in lobby if possible
    try:
        if target_queue.match_lobby_channel_id and target_queue.match_started:
            match_channel = interaction.guild.get_channel(target_queue.match_lobby_channel_id)
            if match_channel:
                await match_channel.send(f"🚫 {player.mention} has been kicked from the match by {interaction.user.mention}")
    except:
        pass

@app_commands.command(name="blacklist", description="Blacklist a player from queue system (Admin only)")
@app_commands.describe(
    player="Player to blacklist",
    reason="Reason for blacklist",
    duration_hours="Duration in hours (0 = permanent, default: 24)"
)
async def blacklist(interaction: discord.Interaction, player: discord.Member, reason: str = "No reason provided", duration_hours: int = 24):
    """Blacklist a player from using the queue system"""
    
    # Admin check
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    # Can't blacklist yourself
    if player.id == interaction.user.id:
        return await interaction.response.send_message("❌ You can't blacklist yourself!", ephemeral=True)
    
    # Can't blacklist admins
    if player.guild_permissions.administrator:
        return await interaction.response.send_message("❌ You can't blacklist admins!", ephemeral=True)
    
    # Check if already blacklisted
    if is_blacklisted(player.id):
        info = get_blacklist_info(player.id)
        expires_at = info.get("expires_at", "Unknown")
        
        if expires_at == "permanent":
            return await interaction.response.send_message(
                f"❌ {player.mention} is already **permanently blacklisted**!\n"
                f"**Reason:** {info.get('reason', 'Unknown')}\n"
                f"**By:** {info.get('admin_name', 'Unknown')}",
                ephemeral=True
            )
        else:
            try:
                expire_time = datetime.fromisoformat(expires_at)
                time_left = expire_time - datetime.now()
                hours_left = int(time_left.total_seconds() / 3600)
                minutes_left = int((time_left.total_seconds() % 3600) / 60)
                
                return await interaction.response.send_message(
                    f"❌ {player.mention} is already blacklisted!\n"
                    f"**Time left:** {hours_left}h {minutes_left}m\n"
                    f"**Reason:** {info.get('reason', 'Unknown')}",
                    ephemeral=True
                )
            except:
                pass
    
    # Add to blacklist
    duration_text = "PERMANENT" if duration_hours == 0 else f"{duration_hours} hours"
    
    add_to_blacklist(
        user_id=player.id,
        reason=reason,
        duration_hours=duration_hours,
        admin_id=interaction.user.id,
        admin_name=interaction.user.display_name
    )
    
    # Create embed
    embed = discord.Embed(
        title="🚫 PLAYER BLACKLISTED",
        color=discord.Color.red(),
        timestamp=datetime.now()
    )
    
    embed.add_field(name="Player", value=player.mention, inline=True)
    embed.add_field(name="Duration", value=duration_text, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="By", value=interaction.user.mention, inline=True)
    
    # Remove from any active lobbies
    removed_from = []
    for lobby_name, queue in get_lobbies(interaction.guild.id).items():
        if player in queue.players:
            queue.players.remove(player)
            removed_from.append(lobby_name)
            
            # Update lobby message
            try:
                if queue.channel_id and queue.message_id:
                    channel = interaction.guild.get_channel(queue.channel_id)
                    if channel:
                        message = await channel.fetch_message(queue.message_id)
                        await message.edit(embed=queue_embed(lobby_name, queue))
            except:
                pass
    
    if removed_from:
        embed.add_field(name="Removed From", value=", ".join(removed_from), inline=False)
    
    # Send DM to blacklisted player
    try:
        dm_embed = discord.Embed(
            title="🚫 YOU HAVE BEEN BLACKLISTED",
            color=discord.Color.red()
        )
        dm_embed.add_field(name="Duration", value=duration_text, inline=True)
        dm_embed.add_field(name="Reason", value=reason, inline=True)
        dm_embed.add_field(name="By", value=interaction.user.display_name, inline=True)
        dm_embed.add_field(name="Appeal", value="Contact server admins for appeal", inline=False)
        
        if duration_hours == 0:
            dm_embed.set_footer(text="PERMANENT BAN - No automatic expiration")
        else:
            dm_embed.set_footer(text=f"Expires in {duration_hours} hours")
        
        await player.send(embed=dm_embed)
    except discord.Forbidden:
        embed.add_field(name="Note", value="Could not DM player (DMs disabled)", inline=False)
    
    await interaction.response.send_message(embed=embed)
    
    # Log to blacklist channel if exists
    blacklist_channel = get(interaction.guild.text_channels, name="blacklist-logs")
    if blacklist_channel:
        await blacklist_channel.send(embed=embed)

@app_commands.command(name="unblacklist", description="Remove player from blacklist (Admin only)")
@app_commands.describe(player="Player to unblacklist")
async def unblacklist(interaction: discord.Interaction, player: discord.Member):
    """Remove a player from blacklist"""
    
    # Admin check
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    # Check if player is blacklisted
    if not is_blacklisted(player.id):
        return await interaction.response.send_message(f"❌ {player.mention} is not blacklisted!", ephemeral=True)
    
    # Get blacklist info before removing
    info = get_blacklist_info(player.id)
    reason = info.get("reason", "Unknown") if info else "Unknown"
    admin_name = info.get("admin_name", "Unknown") if info else "Unknown"
    
    # Remove from blacklist
    remove_from_blacklist(player.id)
    
    # Create embed
    embed = discord.Embed(
        title="✅ PLAYER UNBLACKLISTED",
        color=discord.Color.green(),
        timestamp=datetime.now()
    )
    
    embed.add_field(name="Player", value=player.mention, inline=True)
    embed.add_field(name="By", value=interaction.user.mention, inline=True)
    embed.add_field(name="Original Reason", value=reason, inline=False)
    embed.add_field(name="Original Admin", value=admin_name, inline=True)
    
    # Send DM to player
    try:
        dm_embed = discord.Embed(
            title="✅ BLACKLIST REMOVED",
            description="You have been removed from the blacklist!",
            color=discord.Color.green()
        )
        dm_embed.add_field(name="By", value=interaction.user.display_name)
        dm_embed.set_footer(text="You can now use the queue system again")
        await player.send(embed=dm_embed)
    except:
        pass
    
    await interaction.response.send_message(embed=embed)
    
    # Log to blacklist channel
    blacklist_channel = get(interaction.guild.text_channels, name="blacklist-logs")
    if blacklist_channel:
        await blacklist_channel.send(embed=embed)

@app_commands.command(name="blacklistinfo", description="Check blacklist status")
@app_commands.describe(player="Player to check")
async def blacklistinfo(interaction: discord.Interaction, player: discord.Member = None):
    """Check if a player is blacklisted"""
    
    target = player or interaction.user
    
    if not is_blacklisted(target.id):
        embed = discord.Embed(
            title="✅ NOT BLACKLISTED",
            description=f"{target.mention} is not blacklisted",
            color=discord.Color.green()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=(player is None))
    
    # Get blacklist info
    info = get_blacklist_info(target.id)
    if not info:
        return await interaction.response.send_message("Error retrieving blacklist info", ephemeral=True)
    
    embed = discord.Embed(
        title="🚫 BLACKLISTED PLAYER",
        color=discord.Color.red(),
        timestamp=datetime.now()
    )
    
    embed.add_field(name="Player", value=target.mention, inline=True)
    embed.add_field(name="Reason", value=info.get("reason", "Unknown"), inline=True)
    embed.add_field(name="By", value=info.get("admin_name", "Unknown"), inline=True)
    
    added_at = info.get("added_at")
    if added_at:
        try:
            added_time = datetime.fromisoformat(added_at)
            time_passed = datetime.now() - added_time
            embed.add_field(name="Added", value=f"{int(time_passed.total_seconds()/3600)}h ago", inline=True)
        except:
            pass
    
    expires_at = info.get("expires_at")
    if expires_at == "permanent":
        embed.add_field(name="Status", value="🔴 PERMANENT", inline=True)
        embed.set_footer(text="PERMANENT BLACKLIST - No expiration")
    elif expires_at:
        try:
            expire_time = datetime.fromisoformat(expires_at)
            time_left = expire_time - datetime.now()
            
            if time_left.total_seconds() <= 0:
                # Should have been removed, but just in case
                remove_from_blacklist(target.id)
                embed = discord.Embed(
                    title="✅ BLACKLIST EXPIRED",
                    description=f"{target.mention}'s blacklist has expired",
                    color=discord.Color.green()
                )
            else:
                hours_left = int(time_left.total_seconds() / 3600)
                minutes_left = int((time_left.total_seconds() % 3600) / 60)
                embed.add_field(name="Time Left", value=f"{hours_left}h {minutes_left}m", inline=True)
                embed.set_footer(text=f"Expires in {hours_left}h {minutes_left}m")
        except:
            pass
    
    await interaction.response.send_message(embed=embed, ephemeral=(player is None))

@app_commands.command(name="blacklistall", description="View all blacklisted players (Admin only)")
async def blacklistall(interaction: discord.Interaction):
    """View all blacklisted players"""
    
    # Admin check
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    if not blacklist_data:
        return await interaction.response.send_message("No players are currently blacklisted.", ephemeral=True)
    
    # Create paginated view
    pages = []
    current_page = []
    
    for i, (user_id, info) in enumerate(blacklist_data.items(), 1):
        try:
            member = interaction.guild.get_member(int(user_id))
            username = member.mention if member else f"ID: {user_id}"
        except:
            username = f"ID: {user_id}"
        
        reason = info.get("reason", "Unknown")
        expires_at = info.get("expires_at", "Unknown")
        
        if expires_at == "permanent":
            status = "🔴 PERMANENT"
        else:
            try:
                expire_time = datetime.fromisoformat(expires_at)
                time_left = expire_time - datetime.now()
                if time_left.total_seconds() > 0:
                    hours_left = int(time_left.total_seconds() / 3600)
                    status = f"⏳ {hours_left}h"
                else:
                    status = "EXPIRED"
            except:
                status = "Unknown"
        
        current_page.append(f"**{i}. {username}**\n└ {reason} | {status}")
        
        # 5 entries per page
        if len(current_page) >= 5 or i == len(blacklist_data):
            embed = discord.Embed(
                title="🚫 BLACKLISTED PLAYERS",
                description="\n\n".join(current_page),
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.set_footer(text=f"Page {len(pages) + 1} | Total: {len(blacklist_data)} players")
            pages.append(embed)
            current_page = []
    
    if not pages:
        return await interaction.response.send_message("No blacklisted players found.", ephemeral=True)
    
    # Create paginated view
    class BlacklistPaginator(discord.ui.View):
        def __init__(self, pages):
            super().__init__(timeout=60)
            self.pages = pages
            self.current_page = 0
        
        @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray, disabled=True)
        async def previous(self, interaction: discord.Interaction, button):
            self.current_page -= 1
            button.disabled = self.current_page == 0
            self.next.disabled = self.current_page == len(self.pages) - 1
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
        
        @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
        async def next(self, interaction: discord.Interaction, button):
            self.current_page += 1
            self.previous.disabled = self.current_page == 0
            button.disabled = self.current_page == len(self.pages) - 1
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
        
        @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
        async def close(self, interaction: discord.Interaction, button):
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()
        
        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
    
    view = BlacklistPaginator(pages)
    await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

@app_commands.command(name="needreplace", description="Request a replacement in current match")
async def needreplace(interaction: discord.Interaction):
    user_lobby = None
    queue = None
    
    for lobby_name, q in get_lobbies(interaction.guild.id).items():
        if interaction.user in q.players and q.match_started:
            user_lobby = lobby_name
            queue = q
            break
    
    if not user_lobby:
        await interaction.response.send_message("❌ You're not in an active match!", ephemeral=True)
        return
    
    if interaction.guild.id not in substitute_requests:
        substitute_requests[interaction.guild.id] = {}
    
    substitute_requests[interaction.guild.id][user_lobby] = {"player": interaction.user, "timestamp": datetime.now()}
    view = SubstituteView(user_lobby, interaction.user)
    
    embed = discord.Embed(title="🔄 REPLACEMENT REQUESTED", description=f"{interaction.user.mention} needs a replacement!", color=discord.Color.orange())
    embed.add_field(name="Match", value=user_lobby.upper(), inline=True)
    embed.add_field(name="Player", value=interaction.user.display_name, inline=True)
    
    if queue.host:
        embed.add_field(name="Host", value=queue.host.mention, inline=True)
    
    embed.set_footer(text="Host can use the buttons below to find a replacement")
    
    match_channel = interaction.guild.get_channel(queue.match_lobby_channel_id)
    if match_channel:
        await match_channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ Replacement request sent to match channel!", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view)

# ==================== BOT SETUP ====================

@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

# Add to the bot tree at the bottom
bot.tree.add_command(kickplayer)  
bot.tree.add_command(view)
bot.tree.add_command(startlobby)
bot.tree.add_command(join)
bot.tree.add_command(leave)
bot.tree.add_command(removelobby)
bot.tree.add_command(reportwin)
bot.tree.add_command(addelo)
bot.tree.add_command(removeelo)
bot.tree.add_command(correctwin)
bot.tree.add_command(profile)
bot.tree.add_command(leaderboard)
bot.tree.add_command(end_match)
bot.tree.add_command(party_command)
bot.tree.add_command(partyjoin)
bot.tree.add_command(partyleave)
bot.tree.add_command(partyinfo)
bot.tree.add_command(needreplace)
bot.tree.add_command(blacklist)
bot.tree.add_command(unblacklist)
bot.tree.add_command(blacklistinfo)
bot.tree.add_command(blacklistall)
bot.run(os.getenv("TOKEN"))
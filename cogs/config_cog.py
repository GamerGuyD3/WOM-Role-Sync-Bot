import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import logging
from main import sanitize_rsn, WOM_API_KEY
from typing import List

logger = logging.getLogger('WOMBot')

# This is a set for efficient O(1) lookups
WOM_ROLES = {
    'achiever', 'adamant', 'adept', 'administrator', 'admiral', 'adventurer', 'air', 'anchor', 'apothecary', 'archer',
    'armadylean', 'artillery', 'artisan', 'asgarnian', 'assassin', 'assistant', 'astral', 'athlete', 'attacker', 'bandit',
    'bandosian', 'barbarian', 'battlemage', 'beast', 'berserker', 'blisterwood', 'blood', 'blue', 'bob', 'body',
    'brassican', 'brawler', 'brigadier', 'brigand', 'bronze', 'bruiser', 'bulwark', 'burglar', 'burnt', 'cadet',
    'captain', 'carry', 'champion', 'chaos', 'cleric', 'collector', 'colonel', 'commander', 'competitor', 'completionist',
    'constructor', 'cook', 'coordinator', 'corporal', 'cosmic', 'councillor', 'crafter', 'crew', 'crusader', 'cutpurse',
    'death', 'defender', 'defiler', 'deputy_owner', 'destroyer', 'diamond', 'diseased', 'doctor', 'dogsbody', 'dragon',
    'dragonstone', 'druid', 'duellist', 'earth', 'elite', 'emerald', 'enforcer', 'epic', 'executive', 'expert',
    'explorer', 'farmer', 'feeder', 'fighter', 'fire', 'firemaker', 'firestarter', 'fisher', 'fletcher', 'forager',
    'fremennik', 'gamer', 'gatherer', 'general', 'gnome_child', 'gnome_elder', 'goblin', 'gold', 'goon', 'green',
    'grey', 'guardian', 'guthixian', 'harpoon', 'healer', 'hellcat', 'helper', 'herbologist', 'hero', 'holy',
    'hoarder', 'hunter', 'ignitor', 'illusionist', 'imp', 'infantry', 'inquisitor', 'iron', 'jade', 'justiciar',
    'kandarin', 'karamjan', 'kharidian', 'kitten', 'knight', 'labourer', 'law', 'leader', 'learner', 'legacy',
    'legend', 'legionnaire', 'lieutenant', 'looter', 'lumberjack', 'magic', 'magician', 'major', 'maple', 'marshal',
    'master', 'maxed', 'mediator', 'medic', 'mentor', 'member', 'merchant', 'mind', 'miner', 'minion',
    'misthalinian', 'mithril', 'moderator', 'monarch', 'morytanian', 'mystic', 'myth', 'natural', 'nature', 'necromancer',
    'ninja', 'noble', 'novice', 'nurse', 'oak', 'officer', 'onyx', 'opal', 'oracle', 'orange',
    'owner', 'page', 'paladin', 'pawn', 'pilgrim', 'pine', 'pink', 'prefect', 'priest',
    'private', 'prodigy', 'proselyte', 'prospector', 'protector', 'pure', 'purple', 'pyromancer', 'quester',
    'racer', 'raider', 'ranger', 'record_chaser', 'recruit', 'recruiter', 'red_topaz', 'red', 'rogue', 'ruby',
    'rune', 'runecrafter', 'sage', 'sapphire', 'saradominist', 'saviour', 'scavenger', 'scholar', 'scourge', 'scout',
    'scribe', 'seer', 'senator', 'sentry', 'serenist', 'sergeant', 'shaman', 'sheriff', 'short_green_guy', 'skiller',
    'skulled', 'slayer', 'smiter', 'smith', 'smuggler', 'sniper', 'soul', 'specialist', 'speed_runner', 'spellcaster',
    'squire', 'staff', 'steel', 'strider', 'striker', 'summoner', 'superior', 'supervisor', 'teacher', 'templar',
    'therapist', 'thief', 'tirannian', 'trialist', 'trickster', 'tzkal', 'tztok', 'unholy', 'vagrant', 'vanguard',
    'walker', 'wanderer', 'warden', 'warlock', 'warrior', 'water', 'wild', 'willow', 'wily', 'wintumber',
    'witch', 'wizard', 'worker', 'wrath', 'xerician', 'yellow', 'yew', 'zamorakian', 'zarosian', 'zealot',
    'zenyte'
}

class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def role_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("SELECT wom_role FROM role_mappings WHERE guild_id = ?", (interaction.guild_id,))
        mapped_roles = [row[0] for row in c.fetchall()]
        conn.close()
        
        return [
            app_commands.Choice(name=role, value=role)
            for role in mapped_roles if current.lower() in role.lower()
        ]

    @app_commands.command(name="logchannel", description="Set a channel for the bot to log sync events.")
    @app_commands.describe(channel="The channel to send log messages in. Leave blank to disable.")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        """
        Configures a channel where the bot will post detailed logs after each sync.
        """
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()

        # Check if a group ID is set
        c.execute("SELECT group_id FROM guild_configs WHERE guild_id = ?", (interaction.guild_id,))
        if not c.fetchone():
            conn.close()
            await interaction.response.send_message("⚠️ Please set a Group ID first with `/groupid`.", ephemeral=True)
            return
        
        log_channel_id = None
        if channel:
            # Check bot permissions for the specified channel
            bot_member = interaction.guild.get_member(self.bot.user.id)
            if not channel.permissions_for(bot_member).send_messages or not channel.permissions_for(bot_member).embed_links:
                conn.close()
                await interaction.response.send_message(f"⚠️ I don't have permission to send messages and embeds in {channel.mention}. Please grant me 'Send Messages' and 'Embed Links' permissions there.", ephemeral=True)
                return
            log_channel_id = channel.id

        c.execute("UPDATE guild_configs SET log_channel_id = ? WHERE guild_id = ?", (log_channel_id, interaction.guild_id))
        conn.commit()
        conn.close()

        logger.info(f"Guild {interaction.guild.id} set log channel to {log_channel_id} by user {interaction.user.id}")
        if log_channel_id:
            await interaction.response.send_message(f"✅ Sync events will now be logged in {channel.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Sync event logging has been disabled.", ephemeral=True)

    @app_commands.command(name="groupid", description="Set the WOM Group ID")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_group_id(self, interaction: discord.Interaction, group_id: int):
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO guild_configs (guild_id, group_id, last_sync, inactive_since, log_channel_id) VALUES (?, ?, (SELECT last_sync FROM guild_configs WHERE guild_id = ?), NULL, (SELECT log_channel_id FROM guild_configs WHERE guild_id = ?))", (interaction.guild_id, group_id, interaction.guild_id, interaction.guild_id))
        conn.commit()
        conn.close()
        await interaction.response.send_message(f"✅ Group ID set to **{group_id}**.", ephemeral=True)

    @app_commands.command(name="linkrole", description="Map a WOM Group Role to a Discord Role")
    @app_commands.describe(wom_role="The role name on Wise Old Man.", discord_role="The Discord role to assign")
    @app_commands.checks.has_permissions(administrator=True)
    async def linkrole(self, interaction: discord.Interaction, wom_role: str, discord_role: discord.Role):
        # Validate the role against the official list
        if wom_role.lower() not in WOM_ROLES:
            await interaction.response.send_message(f"❌ **{wom_role}** is not a valid Wise Old Man role. Please check the spelling or consult the `/help` command for a link to the roles list.", ephemeral=True)
            return

        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        
        c.execute("SELECT group_id FROM guild_configs WHERE guild_id = ?", (interaction.guild_id,))
        config = c.fetchone()
        if not config or config[0] is None:
            conn.close()
            await interaction.response.send_message("❌ Please set your server's Wise Old Man Group ID first using `/groupid`.", ephemeral=True)
            return

        c.execute("INSERT OR REPLACE INTO role_mappings (guild_id, wom_role, discord_role_id) VALUES (?, ?, ?)",
                  (interaction.guild_id, wom_role.lower(), discord_role.id))
        conn.commit()
        conn.close()
        await interaction.response.send_message(f"✅ Mapped WOM rank **{wom_role.lower()}** to Discord role {discord_role.mention}.", ephemeral=True)

    @app_commands.command(name="unlinkrole", description="Remove a role mapping")
    @app_commands.describe(wom_role="The WOM role mapping to remove")
    @app_commands.autocomplete(wom_role=role_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def unlinkrole(self, interaction: discord.Interaction, wom_role: str):
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("DELETE FROM role_mappings WHERE guild_id = ? AND wom_role = ?", (interaction.guild_id, wom_role))
        changes = conn.total_changes
        conn.commit()
        conn.close()

        if changes > 0:
            await interaction.response.send_message(f"✅ The mapping for WOM role **{wom_role}** has been removed.", ephemeral=True)
        else:
            await interaction.response.send_message(f"🤔 No mapping was found for the WOM role **{wom_role}**.", ephemeral=True)

    @app_commands.command(name="linkuser", description="Link or update an RSN for a user")
    @app_commands.checks.has_permissions(administrator=True)
    async def linkuser(self, interaction: discord.Interaction, user: discord.Member, rsn: str):
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        
        c.execute("SELECT group_id FROM guild_configs WHERE guild_id = ?", (interaction.guild_id,))
        config = c.fetchone()
        if not config or config[0] is None:
            conn.close()
            await interaction.response.send_message("❌ Please set your server's Wise Old Man Group ID first using `/groupid`.", ephemeral=True)
            return

        clean_rsn = sanitize_rsn(rsn)
        
        wom_id = None # Set wom_id to None, it will be populated during sync

        c.execute("INSERT OR REPLACE INTO links (guild_id, discord_id, rsn, wom_id) VALUES (?, ?, ?, ?)", (interaction.guild_id, user.id, clean_rsn, wom_id))
        conn.commit()
        conn.close()
        await interaction.response.send_message(f"✅ Linked {user.mention} to **{clean_rsn}**. This RSN will be verified during the next sync.", ephemeral=True)

    @app_commands.command(name="unlinkuser", description="Unlink a user from their RSN")
    @app_commands.checks.has_permissions(administrator=True)
    async def unlinkuser(self, interaction: discord.Interaction, user: discord.Member):
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("DELETE FROM links WHERE guild_id = ? AND discord_id = ?", (interaction.guild_id, user.id))
        changes = conn.total_changes
        conn.commit()
        conn.close()

        if changes > 0:
            logger.info(f"User {user.id} was unlinked in guild {interaction.guild.id} by {interaction.user.id}")
            await interaction.response.send_message(f"✅ {user.mention} has been unlinked.", ephemeral=True)
        else:
            await interaction.response.send_message(f"🤔 {user.mention} was not linked to an RSN in this server.", ephemeral=True)

    @app_commands.command(name="nickname", description="Toggle nickname enforcement (forces member's nickname to be their RSN).")
    @app_commands.describe(state="State of nickname enforcement (on/off)")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def nickname(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        """Toggles nickname enforcement on or off."""
        guild_id = interaction.guild_id
        new_state = 1 if state.value == "on" else 0

        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("UPDATE guild_configs SET nickname_enforcement = ? WHERE guild_id = ?", (new_state, guild_id))
        conn.commit()
        conn.close()

        logger.info(f"Guild {guild_id} set nickname enforcement to {state.name} by user {interaction.user.id}")
        await interaction.response.send_message(f"✅ Nickname enforcement has been set to `{state.name}`.", ephemeral=True)

    @app_commands.command(name="reminder", description="Set the inactivity reminder for the sync log channel.")
    @app_commands.describe(interval="The interval to wait before sending a reminder. 'Off' disables it.")
    @app_commands.choices(
        interval=[
            app_commands.Choice(name="Off", value="off"),
            app_commands.Choice(name="3 Days", value="3d"),
            app_commands.Choice(name="5 Days", value="5d"),
            app_commands.Choice(name="7 Days (Default)", value="7d"),
            app_commands.Choice(name="14 Days", value="14d"),
            app_commands.Choice(name="30 Days", value="30d"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reminder(self, interaction: discord.Interaction, interval: app_commands.Choice[str]):
        """Sets the reminder interval for the guild."""
        guild_id = interaction.guild_id
        
        interval_map = {
            "off": 0,
            "3d": 3,
            "5d": 5,
            "7d": 7,
            "14d": 14,
            "30d": 30,
        }
        reminder_days = interval_map.get(interval.value, 7)

        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("UPDATE guild_configs SET reminder_interval_days = ? WHERE guild_id = ?", (reminder_days, guild_id))
        conn.commit()
        conn.close()

        logger.info(f"Guild {guild_id} set reminder interval to {interval.name} by user {interaction.user.id}")
        if reminder_days == 0:
            await interaction.response.send_message("✅ Inactivity reminders have been disabled.", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Inactivity reminders will be sent after **{interval.name}** of no sync changes.", ephemeral=True)

    @app_commands.command(name="notifyplayers", description="Toggle DM notifications for role changes for all players.")
    @app_commands.describe(state="State of DM notifications (on/off)")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def notifyplayers(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        """Toggles role change DM notifications for the guild."""
        guild_id = interaction.guild_id
        new_state = 1 if state.value == "on" else 0

        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("UPDATE guild_configs SET dm_notifications_on = ? WHERE guild_id = ?", (new_state, guild_id))
        conn.commit()
        conn.close()

        logger.info(f"Guild {guild_id} set player DM notifications to {state.name} by user {interaction.user.id}")
        if new_state == 1:
            await interaction.response.send_message("✅ Players will now be notified via DM when their roles change.", ephemeral=True)
        else:
            await interaction.response.send_message("✅ Players will no longer be notified of role changes.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCog(bot))

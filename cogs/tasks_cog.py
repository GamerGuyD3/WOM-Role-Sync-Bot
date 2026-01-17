import discord
from discord.ext import tasks, commands
import sqlite3
import datetime
import logging
import asyncio
import os
import shutil
from main import WOM_API_KEY

logger = logging.getLogger('WOMBot')

class TasksCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sync_roles_loop.start()
        self.cleanup_inactive_guilds.start()
        self.backup_database.start()
        self.update_stats.start()
        self.check_reminders.start()

    def cog_unload(self):
        self.sync_roles_loop.cancel()
        self.cleanup_inactive_guilds.cancel()
        self.backup_database.cancel()
        self.update_stats.cancel()
        self.check_reminders.cancel()

    @tasks.loop(minutes=5)
    async def update_stats(self):
        await self.bot.wait_until_ready()
        
        server_count = len(self.bot.guilds)
        
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO bot_stats (key, value) VALUES (?, ?)", ('server_count', str(server_count)))
        conn.commit()
        conn.close()
        logger.info(f"Updated server count to {server_count}")

    async def sync_guild(self, guild, group_id, log_channel_id, nickname_enforcement, dm_notifications_on):
        log_channel = self.bot.get_channel(log_channel_id) if log_channel_id else None
        url = f"https://api.wiseoldman.net/v2/groups/{group_id}"
        headers = {"x-api-key": WOM_API_KEY, "User-Agent": "MultiServerSyncBot/2.4"}
        
        role_updates = []
        name_changes = []
        nickname_changes = []
        failed_members = 0
        unfound_rsns = []
        removed_users_count = 0

        try:
            async with self.bot.http_session.get(url, headers=headers, timeout=30) as response:
                if response.status != 200:
                    logger.error(f"API Error for guild {guild.id}: Status {response.status}")
                    if log_channel:
                        await log_channel.send(f"⚠️ **Sync Failed**: Could not connect to the Wise Old Man API (Error {response.status}). Please try again later or contact support if the issue persists.")
                    return
                data = await response.json()
                memberships = data.get('memberships', [])
        except Exception as e:
            logger.error(f"API request failed for guild {guild.id}: {e}")
            if log_channel:
                await log_channel.send(f"⚠️ **Sync Failed**: An unexpected error occurred while trying to connect to the Wise Old Man API.")
            return

        wom_roles = {m['player']['id']: m['role'] for m in memberships}
        wom_usernames = {m['player']['id']: m['player']['username'] for m in memberships}
        wom_id_by_username = {m['player']['username'].lower(): m['player']['id'] for m in memberships}

        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()

        c.execute("SELECT discord_id, rsn, wom_id, dm_notifications_on FROM links WHERE guild_id = ?", (guild.id,))
        links = c.fetchall()

        c.execute("SELECT wom_role, discord_role_id FROM role_mappings WHERE guild_id = ?", (guild.id,))
        role_map = {row[0]: guild.get_role(row[1]) for row in c.fetchall() if guild.get_role(row[1])}
        all_mapped_roles = set(role_map.values())

        for discord_id, rsn, wom_id, user_dm_on in links:
            member = guild.get_member(discord_id)
            if not member:
                c.execute("DELETE FROM links WHERE guild_id = ? AND discord_id = ?", (guild.id, discord_id))
                logger.info(f"Removed unlinked user {discord_id} from guild {guild.name} DB as they are no longer in the server.")
                removed_users_count += 1
                continue

            if not wom_id:
                wom_id = wom_id_by_username.get(rsn.lower())
                if wom_id:
                    c.execute("UPDATE links SET wom_id = ? WHERE guild_id = ? AND discord_id = ?", (wom_id, guild.id, discord_id))
                else:
                    unfound_rsns.append(f"▫️ {member.mention} (RSN: `{rsn}`)")
                    continue

            new_rsn = wom_usernames.get(wom_id)
            if new_rsn and new_rsn.lower() != rsn.lower():
                name_changes.append(f"▫️ {member.mention}: `{rsn}` → `{new_rsn}`")
                c.execute("UPDATE links SET rsn = ? WHERE guild_id = ? AND discord_id = ?", (new_rsn, guild.id, discord_id))
                rsn = new_rsn

            current_wom_role = wom_roles.get(wom_id)
            target_role = role_map.get(current_wom_role)
            
            member_roles = set(member.roles)
            roles_to_add = [target_role] if target_role and target_role not in member_roles else []
            roles_to_remove = [r for r in (all_mapped_roles - {target_role}) if r in member_roles]
            
            try:
                # Role update logic
                if roles_to_add or roles_to_remove:
                    await member.edit(roles=list((member_roles - set(roles_to_remove)) | set(roles_to_add)))
                    old_role_mentions = [r.mention for r in roles_to_remove if r] or ["(none)"]
                    new_role_mention = target_role.mention if target_role else "(none)"
                    role_updates.append(f"▫️ {member.mention} (`{rsn}`): {', '.join(old_role_mentions)} → {new_role_mention}")

                    # DM notification logic
                    if dm_notifications_on and user_dm_on:
                        try:
                            dm_message = (f"Your roles in **{guild.name}** have been updated.\n"
                                          f"Your new role is: {new_role_mention}.\n\n"
                                          f"To disable these notifications, use the `/notifyme off` command in the server.")
                            await member.send(dm_message)
                        except discord.Forbidden:
                            logger.warning(f"Could not send role update DM to {member.name} ({member.id}). They may have DMs disabled.")
                        except Exception as e:
                            logger.error(f"Failed to send role update DM to {member.name} ({member.id}): {e}")


                # Nickname enforcement logic
                if nickname_enforcement and member.nick != rsn:
                    original_nick = member.nick or member.name
                    await member.edit(nick=rsn)
                    nickname_changes.append(f"▫️ {member.mention}: `{original_nick}` → `{rsn}`")
                    logger.info(f"Updated nickname for {member.name} in {guild.name} to {rsn}")

            except discord.Forbidden:
                logger.warning(f"Permission error updating roles or nickname for {member} in {guild.name}")
                failed_members += 1
            except Exception as e:
                logger.error(f"Failed to update roles or nickname for {member} in {guild.name}: {e}")
                failed_members += 1
        
        c.execute("UPDATE guild_configs SET last_sync = ? WHERE guild_id = ?", (datetime.datetime.now().isoformat(), guild.id))
        
        if role_updates or name_changes or nickname_changes:
            c.execute("UPDATE guild_configs SET last_change_timestamp = ? WHERE guild_id = ?", (datetime.datetime.now().isoformat(), guild.id))

        conn.commit()
        conn.close()

        if log_channel and (role_updates or name_changes or nickname_changes or failed_members > 0 or unfound_rsns or removed_users_count > 0):
            embed = discord.Embed(
                title=f"Sync Complete for {guild.name}",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now()
            )
            embed.set_footer(text="WOM Role Sync")

            if role_updates:
                embed.add_field(name="👥 Role Updates", value="\n".join(role_updates)[:1024], inline=False)
            if name_changes:
                embed.add_field(name="✍️ RSN Updates (from WOM)", value="\n".join(name_changes)[:1024], inline=False)
            if nickname_changes:
                embed.add_field(name="✍️ Nickname Updates", value="\n".join(nickname_changes)[:1024], inline=False)
            if removed_users_count > 0:
                embed.add_field(name="🗑️ Users Removed", value=f"{removed_users_count} users removed from DB (no longer in server).", inline=False)
            if failed_members > 0:
                 embed.add_field(name="⚠️ Failures", value=f"{failed_members} members could not be updated due to permission errors.", inline=False)
            if unfound_rsns:
                embed.add_field(name="❓ RSN Not Found in WOM Group. Use `/unlinkuser [@user]` if user is not in the clan.", value="\n".join(unfound_rsns)[:1024], inline=False)
            
            if not embed.fields:
                embed.description = "✅ Sync complete. No changes were needed."

            try:
                await log_channel.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Could not send log message to channel {log_channel_id} in guild {guild.id}. Missing permissions.")
        
        logger.info(f"Synced roles for guild {guild.name} ({guild.id}). {len(links)} members checked.")
        return len(role_updates), failed_members, len(links)


    @tasks.loop(time=[datetime.time(h) for h in range(24)])
    async def sync_roles_loop(self):
        await self.bot.wait_until_ready()
        logger.info("Hourly sync started.")
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("SELECT guild_id, group_id, log_channel_id, nickname_enforcement, dm_notifications_on FROM guild_configs WHERE group_id IS NOT NULL")
        configs = c.fetchall()

        for guild_id, group_id, log_channel_id, nickname_enforcement, dm_notifications_on in configs:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            
            await self.sync_guild(guild, group_id, log_channel_id, nickname_enforcement, dm_notifications_on)
            await asyncio.sleep(2) # Stagger API requests
        
        current_time_iso = datetime.datetime.now().isoformat()
        c.execute("INSERT OR REPLACE INTO bot_stats (key, value) VALUES (?, ?)", ('last_global_sync', current_time_iso))
        conn.commit()
        conn.close()
        logger.info(f"Hourly sync finished. Global sync time updated to {current_time_iso}.")

    @tasks.loop(hours=24)
    async def cleanup_inactive_guilds(self):
        await self.bot.wait_until_ready()
        logger.info("Running daily cleanup of inactive guilds.")
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("SELECT guild_id, inactive_since FROM guild_configs")
        all_guilds = c.fetchall()

        guilds_to_delete = []
        for guild_id, inactive_since_str in all_guilds:
            guild = self.bot.get_guild(guild_id)

            if guild:
                if inactive_since_str:
                    c.execute("UPDATE guild_configs SET inactive_since = NULL WHERE guild_id = ?", (guild_id,))
                    logger.info(f"Guild {guild_id} has become active again. Removed inactive marker.")
            else:
                if not inactive_since_str:
                    now_str = datetime.datetime.now().isoformat()
                    c.execute("UPDATE guild_configs SET inactive_since = ? WHERE guild_id = ?", (now_str, guild_id))
                    logger.warning(f"Bot is no longer in guild {guild_id}. Marked as inactive.")
                else:
                    inactive_since = datetime.datetime.fromisoformat(inactive_since_str)
                    if datetime.datetime.now() - inactive_since > datetime.timedelta(days=30):
                        guilds_to_delete.append(guild_id)
        
        for guild_id in guilds_to_delete:
            c.execute("DELETE FROM guild_configs WHERE guild_id = ?", (guild_id,))
            c.execute("DELETE FROM links WHERE guild_id = ?", (guild_id,))
            c.execute("DELETE FROM role_mappings WHERE guild_id = ?", (guild_id,))
            logger.info(f"Deleted all data for inactive guild {guild_id} after 30 day grace period.")

        conn.commit()
        conn.close()
        logger.info("Daily cleanup finished.")

    @tasks.loop(hours=24)
    async def check_reminders(self):
        await self.bot.wait_until_ready()
        logger.info("Running daily reminder check.")
        conn = sqlite3.connect('wom_multi.db')
        c = conn.cursor()
        c.execute("SELECT guild_id, log_channel_id, last_change_timestamp, reminder_interval_days FROM guild_configs WHERE group_id IS NOT NULL AND reminder_interval_days > 0 AND inactive_since IS NULL")
        guilds_to_check = c.fetchall()

        now = datetime.datetime.now()

        for guild_id, log_channel_id, last_change_str, reminder_days in guilds_to_check:
            if not log_channel_id:
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            # If no changes have ever been recorded, set the first timestamp and skip this check
            if not last_change_str:
                c.execute("UPDATE guild_configs SET last_change_timestamp = ? WHERE guild_id = ?", (now.isoformat(), guild_id))
                continue
            
            last_change_time = datetime.datetime.fromisoformat(last_change_str)

            if (now - last_change_time).days >= reminder_days:
                log_channel = self.bot.get_channel(log_channel_id)
                if log_channel:
                    try:
                        message = "To ensure your members' roles are up to date, please click the 'Sync WOM Group' button on your clan's settings page in Old School RuneScape. This will refresh your data on Wise Old Man and allow the bot to sync correctly."
                        await log_channel.send(message)
                        # Reset the timestamp to now to restart the timer
                        c.execute("UPDATE guild_configs SET last_change_timestamp = ? WHERE guild_id = ?", (now.isoformat(), guild_id))
                        logger.info(f"Sent sync reminder to guild {guild.name} ({guild.id}).")
                    except discord.Forbidden:
                        logger.warning(f"Could not send reminder to channel {log_channel_id} in guild {guild.id}. Missing permissions.")
                    except Exception as e:
                        logger.error(f"Failed to send reminder to guild {guild.id}: {e}")

        conn.commit()
        conn.close()
        logger.info("Daily reminder check finished.")

    @tasks.loop(hours=24)
    async def backup_database(self):
        await self.bot.wait_until_ready()
        
        db_file = 'wom_multi.db'
        backup_dir = 'backups'
        
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d")
        backup_file = os.path.join(backup_dir, f'wom_multi_{timestamp}.db')

        try:
            shutil.copy(db_file, backup_file)
            logger.info(f"Successfully backed up database to {backup_file}")
        except Exception as e:
            logger.error(f"Failed to back up database: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(TasksCog(bot))

import aiohttp
import discord
from discord.ext import commands
import sqlite3
import datetime
import logging
import os
import sys
import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(dotenv_path='config.env')

# --- CONFIGURATION ---
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
WOM_API_KEY = os.getenv('WOM_API_KEY')
OWNER_ID = os.getenv('BOT_OWNER_ID')

if not TOKEN:
    logger.critical("DISCORD_BOT_TOKEN environment variable not set. Exiting.")
    sys.exit(1)
if not WOM_API_KEY:
    logger.critical("WOM_API_KEY environment variable not set. Exiting.")
    sys.exit(1)
if not OWNER_ID:
    logger.warning("BOT_OWNER_ID environment variable not set. Owner commands will not be available.")
    OWNER_ID = None # Set to None if not provided
else:
    try:
        OWNER_ID = int(OWNER_ID)
    except ValueError:
        logger.critical("BOT_OWNER_ID must be a valid integer. Exiting.")
        sys.exit(1)

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('WOMBot')

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('wom_multi.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS guild_configs
                 (guild_id INTEGER PRIMARY KEY, group_id INTEGER, last_sync TEXT, inactive_since TEXT, log_channel_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS links
                 (guild_id INTEGER, discord_id INTEGER, rsn TEXT, wom_id INTEGER,
                  PRIMARY KEY (guild_id, discord_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS role_mappings
                 (guild_id INTEGER, wom_role TEXT, discord_role_id INTEGER,
                  PRIMARY KEY (guild_id, wom_role))''')
    c.execute('''CREATE TABLE IF NOT EXISTS bot_stats
                 (key TEXT PRIMARY KEY, value TEXT)''')
    
    c.execute("PRAGMA table_info(guild_configs)")
    columns = [col[1] for col in c.fetchall()]
    if 'inactive_since' not in columns:
        c.execute("ALTER TABLE guild_configs ADD COLUMN inactive_since TEXT")
    if 'log_channel_id' not in columns:
        c.execute("ALTER TABLE guild_configs ADD COLUMN log_channel_id INTEGER")

    c.execute("PRAGMA table_info(links)")
    columns = [col[1] for col in c.fetchall()]
    if 'wom_id' not in columns:
        c.execute("ALTER TABLE links ADD COLUMN wom_id INTEGER")

    c.execute("PRAGMA table_info(guild_configs)")
    columns = [col[1] for col in c.fetchall()]
    if "nickname_enforcement" not in columns:
        c.execute("ALTER TABLE guild_configs ADD COLUMN nickname_enforcement INTEGER DEFAULT 0")

    c.execute("PRAGMA table_info(guild_configs)")
    columns = [col[1] for col in c.fetchall()]
    if "last_change_timestamp" not in columns:
        c.execute("ALTER TABLE guild_configs ADD COLUMN last_change_timestamp TEXT")
    if "reminder_interval_days" not in columns:
        c.execute("ALTER TABLE guild_configs ADD COLUMN reminder_interval_days INTEGER DEFAULT 7")

    c.execute("PRAGMA table_info(guild_configs)")
    columns = [col[1] for col in c.fetchall()]
    if "dm_notifications_on" not in columns:
        c.execute("ALTER TABLE guild_configs ADD COLUMN dm_notifications_on INTEGER DEFAULT 0")

    c.execute("PRAGMA table_info(links)")
    columns = [col[1] for col in c.fetchall()]
    if "dm_notifications_on" not in columns:
        c.execute("ALTER TABLE links ADD COLUMN dm_notifications_on INTEGER DEFAULT 1")

    conn.commit()
    conn.close()

# --- UTILITY FUNCTIONS ---
def sanitize_rsn(rsn: str) -> str:
    return ' '.join(rsn.replace('-', ' ').replace('_', ' ').split())

# --- BOT DEFINITION ---
class WOMBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, owner_id=OWNER_ID)
        self.http_session = None

    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} ({self.user.id})')
        logger.info('Bot is online and ready!')
        # Only show CLI-related messages if in an interactive terminal
        if sys.stdin.isatty():
            print("------")
            print("Bot is running. Type commands below for maintenance.")
            print("Available CLI commands: load, unload, reload, stop")

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def cli_loop(self):
        """Handles command-line input for managing the bot."""
        await self.wait_until_ready()
        
        history = InMemoryHistory()
        session = PromptSession(history=history)

        while not self.is_closed():
            try:
                command = await session.prompt_async("> ")
                args = command.strip().split()
                if not args:
                    continue

                action = args[0].lower()
                
                if action in ["reload", "load", "unload"] and len(args) > 1:
                    cog_name = args[1]
                    try:
                        if action == "reload":
                            await self.reload_extension(f'cogs.{cog_name}')
                        elif action == "load":
                            await self.load_extension(f'cogs.{cog_name}')
                        elif action == "unload":
                            await self.unload_extension(f'cogs.{cog_name}')
                        print(f"✅ Successfully {action}ed cog: {cog_name}")
                    except Exception as e:
                        print(f"❌ Error: {e}")
                
                elif action in ["stop", "shutdown", "exit"]:
                    print("Shutting down bot...")
                    await self.close()
                    break
                    
                else:
                    print(f"Unknown command: '{action}'. Available commands: load, unload, reload, stop")

            except (EOFError, KeyboardInterrupt):
                logger.info("CLI loop interrupted. Shutting down.")
                await self.close()
                break

    async def setup_hook(self):
        init_db()
        self.http_session = aiohttp.ClientSession()
        
        # Load api_cog first as it starts the Flask server for the website
        try:
            await self.load_extension(f'cogs.api_cog')
            logger.info(f"Loaded cog: api_cog")
        except Exception as e:
            logger.error(f"Failed to load cog api_cog: {e}")

        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and filename != '__init__.py' and filename != 'api_cog.py':
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logger.info(f"Loaded cog: {filename}")
                except Exception as e:
                    logger.error(f"Failed to load cog {filename}: {e}")

        await self.tree.sync() # Sync globally by default

        logger.info("Commands synced globally.")
        
        # Start the CLI loop as a background task only if in an interactive terminal
        if sys.stdin.isatty():
            self.loop.create_task(self.cli_loop())

if __name__ == "__main__":
    bot = WOMBot()
    # Using bot.run() is the recommended way to start a discord.py bot.
    # It automatically handles graceful shutdowns on signals like SIGTERM (from Docker)
    # and KeyboardInterrupt (Ctrl+C), preventing the "Unclosed connector" error.
    # We pass log_handler=None to use the logging configuration we defined above.
    bot.run(TOKEN, log_handler=None)
    logger.info("Bot shut down gracefully.")

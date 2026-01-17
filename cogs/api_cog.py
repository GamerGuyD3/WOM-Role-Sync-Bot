import discord
from discord.ext import commands
import sqlite3
from flask import Flask, jsonify, send_from_directory
import logging
import os
import subprocess
import sys

# Set up Flask app logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR) # Suppress Flask/Werkzeug output in console

# Get the absolute path to the project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
website_dir = os.path.join(project_root, 'website')
db_path = os.path.join(project_root, 'wom_multi.db')

app = Flask(__name__, static_folder=website_dir, static_url_path='/')

# --- API Endpoints ---
def get_count_safely(query):
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(query)
        result = c.fetchone()
        conn.close()
        return result[0] if result and result[0] is not None else 0
    except sqlite3.Error as e:
        print(f"Database error in get_count_safely: {e}")
        return 0
        
@app.route('/api/stats')
def get_stats():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("SELECT value FROM bot_stats WHERE key = 'server_count'")
    server_count_raw = c.fetchone()
    server_count = int(server_count_raw[0]) if server_count_raw and server_count_raw[0] is not None else 0

    group_count = get_count_safely("SELECT COUNT(DISTINCT group_id) FROM guild_configs WHERE group_id IS NOT NULL")
    user_count = get_count_safely("SELECT COUNT(discord_id) FROM links")

    c.execute("SELECT MAX(last_sync) FROM guild_configs")
    last_sync_raw = c.fetchone()
    last_sync_time = last_sync_raw[0] if last_sync_raw and last_sync_raw[0] is not None else "Never"

    c.execute("SELECT value FROM bot_stats WHERE key = 'last_global_sync'")
    last_global_sync_raw = c.fetchone()
    last_global_sync_time = last_global_sync_raw[0] if last_global_sync_raw and last_global_sync_raw[0] is not None else "Never"

    conn.close()

    stats = {
        "servers": server_count,
        "groups": group_count,
        "users": user_count,
        "last_sync_time": last_sync_time, # This is per-guild last sync time
        "last_global_sync": last_global_sync_time # New entry
    }
    return jsonify(stats)

@app.route('/')
def serve_index():
    return send_from_directory(website_dir, 'index.html')

@app.route('/<path:path>')
def serve_static_files(path):
    # Ensure only files within the website_dir are served
    if not os.path.commonprefix((os.path.realpath(os.path.join(website_dir, path)), website_dir)) == website_dir:
        return "File not found", 404
    return send_from_directory(website_dir, path)


class ApiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        gunicorn_cmd = [
            sys.executable,
            '-m',
            'gunicorn',
            '--workers', '4',
            '--bind', '0.0.0.0:5000',
            'cogs.api_cog:app'
        ]
        
        self.gunicorn_process = subprocess.Popen(gunicorn_cmd)
        self.bot.add_listener(self.on_ready, 'on_ready')

    async def on_ready(self):
        print(f"Gunicorn server started on http://0.0.0.0:5000")

    def cog_unload(self):
        self.gunicorn_process.terminate()
        self.gunicorn_process.wait()
        print("Gunicorn server stopped.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ApiCog(bot))

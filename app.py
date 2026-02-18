# --- START OF ENTERPRISE BACKEND (app.py) ---

import os
import sys
import shutil
import zipfile
import subprocess
import sqlite3
import asyncio
import aiohttp
import re
import logging
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# --- CONFIGURATION & SECURITY ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Enterprise Config
app.secret_key = os.environ.get("SECRET_KEY", "Enterprise_Secret_Key_Tamim_V1")
UPLOAD_FOLDER = "enterprise_storage"
DB_NAME = "enterprise_db.sqlite"
ADMIN_USER = "admin"
# এখানে আপনার পাসওয়ার্ড দিন (Environment Variable ভালো, না থাকলা ডিফল্ট)
ADMIN_PASS = os.environ.get("ADMIN_PASS", "2310") 

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

running_processes = {}

# --- DATABASE SYSTEM ---
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_NAME)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # Projects Table
        c.execute('''CREATE TABLE IF NOT EXISTS projects 
                     (id INTEGER PRIMARY KEY, name TEXT UNIQUE, 
                      running INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        # Broadcast Logs Table
        c.execute('''CREATE TABLE IF NOT EXISTS broadcasts 
                     (id INTEGER PRIMARY KEY, message TEXT, sent_count INTEGER, 
                      failed_count INTEGER, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        logger.info("Database Initialized Successfully")

init_db()

# --- DECORATORS ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        data = request.json
        if not data or data.get('password') != ADMIN_PASS:
            return jsonify({"error": "Unauthorized Access"}), 403
        return f(*args, **kwargs)
    return decorated_function

# --- FILE FINDER ENGINE ---
def find_bot_file(folder_path):
    priority_files = ["main.py", "app.py", "bot.py", "index.py", "start.py", "run.py"]
    
    for f in priority_files:
        if os.path.exists(os.path.join(folder_path, f)):
            return os.path.join(folder_path, f), folder_path
            
    for root, dirs, files in os.walk(folder_path):
        if "__pycache__" in root or ".git" in root: continue
        for f in priority_files:
            if f in files:
                return os.path.join(root, f), root
        for f in files:
            if f.endswith(".py"):
                return os.path.join(root, f), root
                
    return None, None

# --- TELEGRAM UTILITIES ---
async def send_telegram_message(session, token, chat_id, text, image_url=None, button=None):
    url = f"https://api.telegram.org/bot{token}/"
    payload = {
        "chat_id": chat_id,
        "parse_mode": "HTML"
    }
    
    try:
        if image_url:
            payload['photo'] = image_url
            payload['caption'] = text
            if button:
                payload['reply_markup'] = {"inline_keyboard": [[{"text": button['name'], "url": button['url']}]]}
            method = "sendPhoto"
        else:
            payload['text'] = text
            if button:
                payload['reply_markup'] = {"inline_keyboard": [[{"text": button['name'], "url": button['url']}]]}
            method = "sendMessage"

        async with session.post(url + method, json=payload) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Send Error {chat_id}: {str(e)}")
        return False

def extract_ids_from_file(filepath):
    ids = set()
    # Regex to find numbers (potential IDs)
    id_pattern = re.compile(r'(-?\d{5,})') 
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                matches = id_pattern.findall(line)
                for m in matches:
                    # Basic filter for valid Telegram IDs (usually > 0 or < -1000000000000 for supergroups)
                    ids.add(m)
    except Exception as e:
        logger.error(f"Error reading ID file: {e}")
    return list(ids)

def find_token_in_dir(directory):
    # Search common files for token
    check_files = ['config.env', '.env', 'config.txt', 'settings.py', 'bot.py', 'main.py']
    token_pattern = re.compile(r'(\d{8,10}:[A-Za-z0-9_-]{35})')
    
    for root, dirs, files in os.walk(directory):
        for name in files:
            if name in check_files or name.endswith('.py'):
                full_path = os.path.join(root, name)
                try:
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        match = token_pattern.search(content)
                        if match:
                            return match.group(1)
                except: pass
    return None

# --- API ROUTES ---

@app.route('/')
def index():
    return jsonify({
        "system": "Enterprise Host Pro Max",
        "version": "2.0.0",
        "status": "Online",
        "author": "Tamim"
    })

# --- AUTH ---
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    if data.get('username') == ADMIN_USER and data.get('password') == ADMIN_PASS:
        return jsonify({"message": "Access Granted", "token": "secure_enterprise_token"})
    return jsonify({"error": "Invalid Credentials"}), 401

# --- PROJECT MANAGEMENT ---
@app.route('/upload', methods=['POST'])
def upload():
    # Password verification via form data
    password = request.form.get('password')
    if password != ADMIN_PASS:
        return jsonify({"error": "Security Alert: Wrong Password"}), 403
        
    if 'file' not in request.files:
        return jsonify({"error": "No file sent"}), 400
        
    file = request.files['file']
    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in ['.zip', '.py']:
        return jsonify({"error": "Invalid file type. Only .zip or .py"}), 400
        
    app_name = os.path.splitext(filename)[0]
    project_dir = os.path.join(UPLOAD_FOLDER, app_name)
    
    # Clean previous version
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)
    os.makedirs(project_dir, exist_ok=True)
    
    save_path = os.path.join(project_dir, filename)
    file.save(save_path)
    
    if ext == '.zip':
        try:
            with zipfile.ZipFile(save_path, 'r') as z:
                z.extractall(project_dir)
            os.remove(save_path)
            
            # Install Requirements
            for root, dirs, files in os.walk(project_dir):
                if "requirements.txt" in files:
                    req_path = os.path.join(root, "requirements.txt")
                    logger.info(f"Installing requirements for {app_name}...")
                    subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], 
                                 cwd=os.path.dirname(req_path), check=False, capture_output=True)
                    break
        except Exception as e:
            return jsonify({"error": f"Extraction Failed: {str(e)}"}), 500

    # Add to DB
    db = get_db()
    try:
        db.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (app_name,))
        db.commit()
    except: pass
            
    return jsonify({"message": "Project Deployed Successfully!", "name": app_name})

@app.route('/my_apps', methods=['POST'])
@admin_required
def my_apps():
    projects_path = UPLOAD_FOLDER
    apps = []
    
    if os.path.exists(projects_path):
        for app_name in os.listdir(projects_path):
            full_path = os.path.join(projects_path, app_name)
            if os.path.isdir(full_path):
                pid = f"admin_{app_name}"
                
                # Process Status
                is_running = False
                if pid in running_processes and running_processes[pid].poll() is None:
                    is_running = True
                
                # Logs
                logs = "System Ready."
                log_file = os.path.join(full_path, "logs.txt")
                if os.path.exists(log_file):
                    with open(log_file, 'r', errors='ignore') as f:
                        f.seek(0, 2); size = f.tell()
                        f.seek(max(size - 3000, 0))
                        logs = f.read()

                # Token Detection
                token = find_token_in_dir(full_path)
                
                apps.append({
                    "name": app_name, 
                    "running": is_running, 
                    "logs": logs,
                    "has_token": bool(token)
                })
                
    return jsonify({"apps": apps})

@app.route('/action', methods=['POST'])
@admin_required
def action():
    data = request.json
    act = data.get('action')
    app_name = data.get('app_name')
    
    pid = f"admin_{app_name}"
    app_dir = os.path.join(UPLOAD_FOLDER, app_name)
    
    if act == "start":
        if pid in running_processes and running_processes[pid].poll() is None:
            return jsonify({"message": "Already Running!"})
            
        script_path, script_dir = find_bot_file(app_dir)
        if not script_path:
            return jsonify({"error": "No main python file found!"}), 404
            
        log_file = open(os.path.join(app_dir, "logs.txt"), "a")
        
        # Env passing
        my_env = os.environ.copy()
        
        proc = subprocess.Popen(
            [sys.executable, "-u", os.path.basename(script_path)],
            cwd=script_dir,
            stdout=log_file,
            stderr=log_file,
            text=True,
            env=my_env
        )
        running_processes[pid] = proc
        return jsonify({"message": f"{app_name} Started!"})

    elif act == "stop":
        if pid in running_processes:
            running_processes[pid].terminate()
            try: running_processes[pid].wait(timeout=2)
            except: running_processes[pid].kill()
            del running_processes[pid]
            return jsonify({"message": "Stopped!"})
        return jsonify({"error": "Not running"})

    elif act == "delete":
        if pid in running_processes:
            running_processes[pid].kill()
            del running_processes[pid]
        if os.path.exists(app_dir):
            shutil.rmtree(app_dir)
        return jsonify({"message": "Deleted!"})

    return jsonify({"error": "Bad Request"}), 400

# --- BROADCAST SYSTEM (ENTERPRISE) ---
@app.route('/broadcast', methods=['POST'])
@admin_required
async def broadcast():
    data = request.json
    text = data.get('text')
    image = data.get('image')
    btn_name = data.get('btn_name')
    btn_url = data.get('btn_url')
    target_bots = data.get('bots', []) # Names of bots to use tokens from
    
    if not text:
        return jsonify({"error": "Message text required"}), 400

    # Collect all unique IDs from all projects or selected projects
    all_ids = set()
    tokens_found = []
    
    projects = os.listdir(UPLOAD_FOLDER)
    for p_name in projects:
        if target_bots and p_name not in target_bots:
            continue
            
        p_path = os.path.join(UPLOAD_FOLDER, p_name)
        if os.path.isdir(p_path):
            # Find Token
            token = find_token_in_dir(p_path)
            if token and token not in tokens_found:
                tokens_found.append(token)
            
            # Find IDs
            for file in os.listdir(p_path):
                if file in ['users.txt', 'chats.txt', 'ids.txt', 'users.json']:
                    ids = extract_ids_from_file(os.path.join(p_path, file))
                    all_ids.update(ids)

    if not tokens_found:
        return jsonify({"error": "No valid bot tokens found in selected projects!"}), 400
        
    if not all_ids:
        return jsonify({"error": "No users found (users.txt/chats.txt missing)!"}), 400

    # Start Async Broadcasting
    success_count = 0
    fail_count = 0
    button = {"name": btn_name, "url": btn_url} if btn_name and btn_url else None
    
    async with aiohttp.ClientSession() as session:
        # Use first token found for broadcasting
        api_token = tokens_found[0]
        
        tasks = []
        for uid in all_ids:
            task = send_telegram_message(session, api_token, uid, text, image, button)
            tasks.append(task)
            
        # Execute in batches to avoid flooding
        results = []
        batch_size = 30
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i+batch_size]
            results.extend(await asyncio.gather(*batch))
            await asyncio.sleep(1) # Small delay between batches
            
        success_count = sum(results)
        fail_count = len(results) - success_count

    # Log to DB
    db = get_db()
    db.execute("INSERT INTO broadcasts (message, sent_count, failed_count) VALUES (?, ?, ?)",
               (text[:50], success_count, fail_count))
    db.commit()

    return jsonify({
        "message": "Broadcast Completed",
        "total_users": len(all_ids),
        "success": success_count,
        "failed": fail_count
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

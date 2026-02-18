# --- START OF ENTERPRISE HOSTING BACKEND (app.py) ---

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
import datetime
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from werkzeug.utils import secure_filename
from functools import wraps

# --- CONFIGURATION ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Basic Config
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STORAGE_PATH = os.path.join(BASE_DIR, "enterprise_storage")
DB_PATH = os.path.join(BASE_DIR, "hostpromax.db")
LOGS_PATH = os.path.join(BASE_DIR, "system_logs.log")

# Security Config
# Render এ Environment Variable থেকে নিবে, না থাকলে '2310' ব্যবহার করবে
ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "2310")
SECRET_KEY = os.environ.get("SECRET_KEY", "host_promax_secure_key_v1")

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("HostProMax")

# Ensure directories exist
if not os.path.exists(STORAGE_PATH):
    os.makedirs(STORAGE_PATH)

# In-memory process tracker
running_processes = {}

# --- DATABASE SYSTEM ---
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Projects Table
        c.execute('''CREATE TABLE IF NOT EXISTS projects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE,
                        running INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
        
        # Broadcast History Table
        c.execute('''CREATE TABLE IF NOT EXISTS broadcast_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_name TEXT,
                        sent_count INTEGER,
                        failed_count INTEGER,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Database Init Error: {e}")

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --- DECORATORS & MIDDLEWARE ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        data = request.json or {}
        # Check password from JSON body or Headers
        req_pass = data.get('password') or request.headers.get('X-Admin-Pass')
        
        if req_pass != ADMIN_PASSWORD:
            logger.warning(f"Unauthorized access attempt from IP: {request.remote_addr}")
            return jsonify({"error": "Unauthorized: Invalid Password"}), 403
        return f(*args, **kwargs)
    return decorated_function

# --- HELPER FUNCTIONS ---

def find_bot_entry(folder_path):
    """Finds the main python script and checks for existing user files."""
    priority_files = ["main.py", "bot.py", "app.py", "index.py", "start.py", "run.py"]
    
    # 1. Check root directory
    for f in priority_files:
        full_path = os.path.join(folder_path, f)
        if os.path.exists(full_path):
            return full_path, folder_path
            
    # 2. Check subdirectories (recursive)
    for root, dirs, files in os.walk(folder_path):
        if "__pycache__" in root or ".git" in root: continue
        
        for f in priority_files:
            if f in files:
                return os.path.join(root, f), root
                
        # Fallback to any python file
        for f in files:
            if f.endswith(".py"):
                return os.path.join(root, f), root
                
    return None, None

def extract_bot_token(folder_path):
    """Scans config files to find Telegram Bot Token."""
    token_pattern = re.compile(r'(\d{8,10}:[A-Za-z0-9_-]{35})')
    files_to_check = ['config.env', '.env', 'config.txt', 'settings.py', 'bot.py', 'main.py']
    
    for root, dirs, files in os.walk(folder_path):
        for name in files:
            if name in files_to_check or name.endswith('.py'):
                try:
                    full_path = os.path.join(root, name)
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        match = token_pattern.search(content)
                        if match:
                            return match.group(1)
                except Exception: pass
    return None

def extract_user_ids(folder_path):
    """Reads user ID files and extracts unique IDs."""
    id_files = ['users.txt', 'chats.txt', 'ids.txt', 'broadcast.txt']
    found_ids = set()
    id_regex = re.compile(r'(-?\d{5,})') # Regex for numbers
    
    for root, dirs, files in os.walk(folder_path):
        for name in files:
            if name in id_files:
                try:
                    full_path = os.path.join(root, name)
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            matches = id_regex.findall(line)
                            for m in matches:
                                found_ids.add(m)
                except Exception: pass
                
    return list(found_ids)

async def send_tg_message(session, token, chat_id, text, image_url=None, btn_name=None, btn_url=None):
    """Asynchronous Telegram Message Sender."""
    base_url = f"https://api.telegram.org/bot{token}/"
    payload = {"chat_id": chat_id, "parse_mode": "HTML"}
    
    try:
        # Prepare Button
        if btn_name and btn_url:
            payload['reply_markup'] = {"inline_keyboard": [[{"text": btn_name, "url": btn_url}]]}
        
        # Prepare Content
        if image_url:
            payload['photo'] = image_url
            payload['caption'] = text
            method = "sendPhoto"
        else:
            payload['text'] = text
            method = "sendMessage"
            
        async with session.post(base_url + method, json=payload, timeout=10) as resp:
            if resp.status == 200:
                return True, chat_id
            else:
                err = await resp.json()
                logger.warning(f"Send fail {chat_id}: {err.get('description')}")
                return False, chat_id
    except Exception as e:
        logger.error(f"Network Error {chat_id}: {str(e)}")
        return False, chat_id

# --- API ROUTES ---

@app.route('/')
def home():
    return jsonify({
        "app": "Host Pro Max Enterprise",
        "version": "3.1.0",
        "status": "Operational",
        "docs": "Secure, Reliable, Scalable"
    })

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    if data.get('password') == ADMIN_PASSWORD:
        logger.info("Admin logged in successfully.")
        return jsonify({"status": "success", "message": "Access Granted"})
    return jsonify({"status": "error", "message": "Wrong Password"}), 401

# --- PROJECT MANAGEMENT ---

@app.route('/upload', methods=['POST'])
def upload():
    # Form Data Security Check
    req_pass = request.form.get('password')
    if req_pass != ADMIN_PASSWORD:
        return jsonify({"error": "Security Alert: Wrong Password"}), 403
        
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in ['.zip', '.py']:
        return jsonify({"error": "Invalid type. Only .zip or .py allowed"}), 400
        
    project_name = os.path.splitext(filename)[0]
    project_dir = os.path.join(STORAGE_PATH, project_name)
    
    # Cleanup old version
    if os.path.exists(project_dir):
        try:
            shutil.rmtree(project_dir)
        except Exception as e:
            logger.error(f"Folder remove error: {e}")
            
    os.makedirs(project_dir, exist_ok=True)
    save_path = os.path.join(project_dir, filename)
    file.save(save_path)
    
    # Extract if Zip
    if ext == '.zip':
        try:
            with zipfile.ZipFile(save_path, 'r') as zip_ref:
                zip_ref.extractall(project_dir)
            os.remove(save_path) # Delete zip after extraction
            
            # Install Requirements
            req_path = None
            for root, dirs, files in os.walk(project_dir):
                if "requirements.txt" in files:
                    req_path = os.path.join(root, "requirements.txt")
                    break
            
            if req_path:
                logger.info(f"Installing requirements for {project_name}...")
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], 
                             cwd=os.path.dirname(req_path), check=False, capture_output=True)
                             
        except Exception as e:
            return jsonify({"error": f"Extraction Failed: {str(e)}"}), 500

    # DB Entry
    try:
        db = get_db()
        db.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (project_name,))
        db.commit()
        db.close()
    except: pass

    logger.info(f"Project '{project_name}' deployed successfully.")
    return jsonify({"message": "Deployed Successfully!", "name": project_name})

@app.route('/my_apps', methods=['POST'])
@admin_required
def my_apps():
    apps = []
    if not os.path.exists(STORAGE_PATH):
        return jsonify({"apps": []})
        
    for project_name in os.listdir(STORAGE_PATH):
        full_path = os.path.join(STORAGE_PATH, project_name)
        if os.path.isdir(full_path):
            pid_key = f"proc_{project_name}"
            
            # Check Running Status
            is_running = False
            if pid_key in running_processes:
                if running_processes[pid_key].poll() is None:
                    is_running = True
                else:
                    # Clean dead process
                    del running_processes[pid_key]
            
            # Read Logs
            logs = "Waiting for output..."
            log_file = os.path.join(full_path, "logs.txt")
            if os.path.exists(log_file):
                try:
                    with open(log_file, 'r', errors='ignore') as f:
                        f.seek(0, 2); size = f.tell()
                        f.seek(max(size - 2000, 0)) # Last 2KB
                        logs = f.read()
                except: logs = "Error reading logs."

            # Check Bot Token
            token = extract_bot_token(full_path)
            
            apps.append({
                "name": project_name,
                "running": is_running,
                "logs": logs,
                "has_token": bool(token)
            })
            
    return jsonify({"apps": apps})

@app.route('/action', methods=['POST'])
@admin_required
def action():
    data = request.json
    action_type = data.get('action')
    app_name = data.get('app_name')
    
    if not app_name:
        return jsonify({"error": "App name missing"}), 400
        
    pid_key = f"proc_{app_name}"
    app_dir = os.path.join(STORAGE_PATH, app_name)
    
    if not os.path.exists(app_dir):
        return jsonify({"error": "Project not found"}), 404

    # START ACTION
    if action_type == "start":
        if pid_key in running_processes and running_processes[pid_key].poll() is None:
            return jsonify({"message": "Already Running"})
            
        script_path, script_dir = find_bot_entry(app_dir)
        if not script_path:
            return jsonify({"error": "No Python script found!"}), 404
            
        log_file = open(os.path.join(app_dir, "logs.txt"), "a")
        
        # Environment setup
        my_env = os.environ.copy()
        
        logger.info(f"Starting bot: {app_name}")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", os.path.basename(script_path)],
                cwd=script_dir,
                stdout=log_file,
                stderr=log_file,
                text=True,
                env=my_env
            )
            running_processes[pid_key] = proc
            return jsonify({"message": "Bot Started!"})
        except Exception as e:
            logger.error(f"Start failed: {e}")
            return jsonify({"error": str(e)}), 500

    # STOP ACTION
    elif action_type == "stop":
        if pid_key in running_processes:
            logger.info(f"Stopping bot: {app_name}")
            running_processes[pid_key].terminate()
            try:
                running_processes[pid_key].wait(timeout=3)
            except:
                running_processes[pid_key].kill()
            del running_processes[pid_key]
            return jsonify({"message": "Stopped"})
        return jsonify({"error": "Not running"})

    # DELETE ACTION
    elif action_type == "delete":
        if pid_key in running_processes:
            running_processes[pid_key].kill()
            del running_processes[pid_key]
        
        if os.path.exists(app_dir):
            shutil.rmtree(app_dir)
            
        # DB Cleanup
        db = get_db()
        db.execute("DELETE FROM projects WHERE name = ?", (app_name,))
        db.commit()
        db.close()
        
        logger.info(f"Deleted project: {app_name}")
        return jsonify({"message": "Deleted"})

    return jsonify({"error": "Invalid Action"}), 400

# --- BROADCAST SYSTEM ---

@app.route('/broadcast', methods=['POST'])
@admin_required
async def broadcast():
    data = request.json
    text = data.get('text')
    image = data.get('image')
    btn_name = data.get('btn_name')
    btn_url = data.get('btn_url')
    target_apps = data.get('apps', []) # Optional: specific apps
    
    if not text:
        return jsonify({"error": "Message text required"}), 400

    logger.info("Initiating Broadcast System...")
    
    all_user_ids = set()
    api_token = None
    
    # Scan projects for Token and User IDs
    for project_name in os.listdir(STORAGE_PATH):
        # If specific apps selected, filter them
        if target_apps and project_name not in target_apps:
            continue
            
        project_path = os.path.join(STORAGE_PATH, project_name)
        if os.path.isdir(project_path):
            # Find Token (use the first valid token found)
            if not api_token:
                found_token = extract_bot_token(project_path)
                if found_token:
                    api_token = found_token
                    logger.info(f"Using token from project: {project_name}")
            
            # Find User IDs
            ids = extract_user_ids(project_path)
            all_user_ids.update(ids)

    if not api_token:
        return jsonify({"error": "No bot token found in uploaded projects to send messages!"}), 400
        
    if not all_user_ids:
        return jsonify({"error": "No users found in users.txt or chats.txt files!"}), 400

    # Broadcast Execution
    success_count = 0
    fail_count = 0
    total_users = len(all_user_ids)
    
    logger.info(f"Broadcasting to {total_users} users...")
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for uid in all_user_ids:
            task = send_tg_message(session, api_token, uid, text, image, btn_name, btn_url)
            tasks.append(task)
        
        # Process in batches of 30 to prevent flooding
        batch_size = 30
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i+batch_size]
            results = await asyncio.gather(*batch)
            
            for res, uid in results:
                if res: success_count += 1
                else: fail_count += 1
                
            await asyncio.sleep(0.5) # Delay between batches

    # Save History
    db = get_db()
    db.execute("INSERT INTO broadcast_history (project_name, sent_count, failed_count) VALUES (?, ?, ?)",
               ("Global Broadcast", success_count, fail_count))
    db.commit()
    db.close()

    logger.info(f"Broadcast Finished. Success: {success_count}, Failed: {fail_count}")
    return jsonify({
        "message": "Broadcast Completed",
        "total": total_users,
        "success": success_count,
        "failed": fail_count
    })

# --- ERROR HANDLERS ---
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Resource not found"}), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server Error: {e}")
    return jsonify({"error": "Internal Server Error"}), 500

if __name__ == "__main__":
    # Render Port Configuration
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Server starting on port {port}...")
    app.run(host="0.0.0.0", port=port)

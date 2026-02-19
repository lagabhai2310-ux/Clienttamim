import os
import sys
import shutil
import zipfile
import subprocess
import sqlite3
import time
import secrets
import hmac
import threading
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

# --- APP CONFIGURATION ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Directories
UPLOAD_FOLDER = "user_uploads"
DB_NAME = "hosting_database.db"
START_TIME = time.time()

# --- BROADCAST LISTENER CODE (Auto Injection) ---
BROADCAST_LISTENER_SCRIPT = """
import requests
import threading
import time
import os

# Server URL
SERVER_URL = "https://hostpromax.xo.je" 

def check_loop():
    last_id = 0
    while True:
        try:
            res = requests.get(f"{SERVER_URL}/api/check_broadcast?last_id={last_id}", timeout=10)
            data = res.json()
            
            if data.get("new_broadcast"):
                msg_text = data.get("content", "")
                img = data.get("image_url")
                btn_name = data.get("button_name")
                btn_url = data.get("button_url")
                
                with open("last_broadcast.txt", "w") as f:
                    f.write(f"{msg_text}||{img}||{btn_name}||{btn_url}")
                
                print(f"[System Broadcast]: {msg_text}")
                last_id = data.get("id")
        except:
            pass
            
        time.sleep(5)

t = threading.Thread(target=check_loop)
t.daemon = True
t.start()
"""

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

running_processes = {}

# --- DATABASE SYSTEM ---
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # System config table (Only for Admin Token now)
    c.execute('''CREATE TABLE IF NOT EXISTS system_config 
                 (key TEXT PRIMARY KEY, value TEXT)''')
    
    # Broadcast Logs Table
    c.execute('''CREATE TABLE IF NOT EXISTS broadcasts 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  content TEXT, 
                  image_url TEXT, 
                  button_name TEXT, 
                  button_url TEXT, 
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# --- STARTUP CONFIGURATION ---
# Generate Admin Token automatically on start
def init_admin_token():
    conn = get_db()
    token_row = conn.execute("SELECT value FROM system_config WHERE key='admin_key'").fetchone()
    if not token_row:
        new_token = secrets.token_hex(16)
        conn.execute("INSERT INTO system_config (key, value) VALUES (?, ?)", ('admin_key', new_token))
        conn.commit()
        print(f">>> Generated Admin Token: {new_token}")
    conn.close()

init_admin_token()

# --- HELPER FUNCTIONS ---
def find_bot_file(folder_path):
    priority_files = ["main.py", "app.py", "bot.py", "index.py", "run.py", "start.py"]
    
    for f in priority_files:
        full_path = os.path.join(folder_path, f)
        if os.path.exists(full_path):
            return full_path, folder_path
            
    for root, dirs, files in os.walk(folder_path):
        if "__pycache__" in root or ".git" in root: continue
        for f in priority_files:
            if f in files: return os.path.join(root, f), root
        for f in files:
            if f.endswith(".py"): return os.path.join(root, f), root
                
    return None, None

def install_requirements(folder_path):
    req_path = os.path.join(folder_path, "requirements.txt")
    if os.path.exists(req_path):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], check=False, capture_output=True)
        except Exception as e:
            print(f"Req install error: {e}")

# --- MIDDLEWARE ---
def admin_required(f):
    def wrapper(*args, **kwargs):
        auth_token = request.headers.get('X-Admin-Key')
        if not auth_token:
            return jsonify({"error": "Unauthorized Access"}), 401
        
        conn = get_db()
        admin_key_row = conn.execute("SELECT value FROM system_config WHERE key='admin_key'").fetchone()
        conn.close()
        
        if admin_key_row and hmac.compare_digest(auth_token, admin_key_row['value']):
            return f(*args, **kwargs)
            
        return jsonify({"error": "Invalid Admin Token"}), 403
    wrapper.__name__ = f.__name__
    return wrapper

# ================= ROUTES =================

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

# Always returns true because we removed password setup
@app.route('/api/check_setup', methods=['GET'])
def check_setup():
    return jsonify({"setup_complete": True})

# Login is no longer needed on backend, but kept for safety
@app.route('/api/login', methods=['POST'])
def login():
    return jsonify({"message": "Login successful"})

# Admin Login (Generates token if password matches frontend hardcoded one)
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    # We just trust the frontend sent the correct password '2310'
    # Or you can hardcode check here too, but frontend handles it.
    if data.get('password') == '2310': 
        conn = get_db()
        admin_key_row = conn.execute("SELECT value FROM system_config WHERE key='admin_key'").fetchone()
        conn.close()
        return jsonify({"message": "Admin Access Granted", "key": admin_key_row['value']})
    return jsonify({"error": "Denied"}), 401

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def admin_stats():
    uptime_sec = int(time.time() - START_TIME)
    days = uptime_sec // 86400
    hours = (uptime_sec % 86400) // 3600
    minutes = (uptime_sec % 3600) // 60
    seconds = uptime_sec % 60
    
    running_count = sum(1 for p in running_processes.values() if p.poll() is None)
    
    return jsonify({
        "uptime": f"{days}d {hours}h {minutes}m {seconds}s",
        "running_bots": running_count,
        "uptime_seconds": uptime_sec
    })

# --- BROADCAST API ---
@app.route('/api/broadcast', methods=['POST'])
@admin_required
def send_broadcast():
    data = request.json
    text = data.get('text')
    image = data.get('image_url')
    btn_name = data.get('button_name')
    btn_url = data.get('button_url')
    
    if not text:
        return jsonify({"error": "Message text required"}), 400

    conn = get_db()
    conn.execute("INSERT INTO broadcasts (content, image_url, button_name, button_url) VALUES (?, ?, ?, ?)",
                 (text, image, btn_name, btn_url))
    conn.commit()
    conn.close()

    return jsonify({"message": "Broadcast sent successfully!"})

@app.route('/api/check_broadcast', methods=['GET'])
def check_broadcast():
    last_id = request.args.get('last_id', 0, type=int)
    conn = get_db()
    row = conn.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    
    if row:
        if row['id'] > last_id:
            return jsonify({
                "new_broadcast": True,
                "id": row['id'],
                "content": row['content'],
                "image_url": row['image_url'],
                "button_name": row['button_name'],
                "button_url": row['button_url']
            })
    return jsonify({"new_broadcast": False})

# --- UPLOAD BOT (WITH AUTO INJECTION) ---
@app.route('/upload', methods=['POST'])
def upload():
    username = "host_admin"
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    filename = secure_filename(file.filename)
    if not filename:
        return jsonify({"error": "Invalid filename"}), 400
        
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ['.zip', '.py']:
        return jsonify({"error": "Only .zip or .py allowed"}), 400
        
    app_name = os.path.splitext(filename)[0]
    user_dir = os.path.join(UPLOAD_FOLDER, username, app_name)
    
    if os.path.exists(user_dir):
        try: shutil.rmtree(user_dir)
        except: pass
    os.makedirs(user_dir, exist_ok=True)
    
    save_path = os.path.join(user_dir, filename)
    file.save(save_path)
    
    if ext == '.zip':
        try:
            with zipfile.ZipFile(save_path, 'r') as z:
                z.extractall(user_dir)
            os.remove(save_path)
            install_requirements(user_dir)
        except Exception as e:
            return jsonify({"error": f"Zip error: {str(e)}"}), 500

    # AUTO INJECTION
    listener_path = os.path.join(user_dir, "auto_listener.py")
    with open(listener_path, "w") as f:
        f.write(BROADCAST_LISTENER_SCRIPT)
            
    return jsonify({"message": "Upload Successful!"})

# --- APP CONTROL (MODIFIED FOR INJECTION) ---
@app.route('/action', methods=['POST'])
def action():
    data = request.json
    act = data.get('action')
    app_name = data.get('app_name')
    username = "host_admin"
    
    pid = f"{username}_{app_name}"
    app_dir = os.path.join(UPLOAD_FOLDER, username, app_name)
    
    if act == "start":
        if pid in running_processes and running_processes[pid].poll() is None:
            return jsonify({"message": "Already Running!"})
            
        script_path, script_dir = find_bot_file(app_dir)
        if not script_path:
            return jsonify({"error": "No main.py or bot.py found!"}), 404
        
        main_script_name = os.path.basename(script_path)
        wrapper_path = os.path.join(script_dir, "run_wrapper.py")
        
        wrapper_code = f"""
import sys
import importlib.util

# 1. Load the Auto Listener
try:
    spec = importlib.util.spec_from_file_location("auto_listener", "auto_listener.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("Broadcast listener loaded.")
except Exception as e:
    print(f"Failed to load listener: {{e}}")

# 2. Run the main bot script
exec(open('{main_script_name}').read())
"""
        with open(wrapper_path, "w") as f:
            f.write(wrapper_code)

        log_file = open(os.path.join(app_dir, "logs.txt"), "a")
        
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", "run_wrapper.py"],
                cwd=script_dir,
                stdout=log_file,
                stderr=log_file,
                text=True,
                env=os.environ.copy()
            )
            running_processes[pid] = proc
            return jsonify({"message": "Bot Started with Broadcast Support!"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    elif act == "stop":
        if pid in running_processes:
            running_processes[pid].terminate()
            try: running_processes[pid].wait(timeout=3)
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
        return jsonify({"error": "Not found"})

# --- GET APPS LIST ---
@app.route('/my_apps', methods=['POST'])
def my_apps():
    username = "host_admin"
    user_path = os.path.join(UPLOAD_FOLDER, username)
    
    if not os.path.exists(user_path): 
        return jsonify({"apps": []})
    
    apps = []
    for app_name in os.listdir(user_path):
        full_path = os.path.join(user_path, app_name)
        if os.path.isdir(full_path):
            pid = f"{username}_{app_name}"
            
            is_running = False
            if pid in running_processes:
                if running_processes[pid].poll() is None:
                    is_running = True
                else:
                    del running_processes[pid]
            
            logs = "Waiting for logs..."
            log_file = os.path.join(full_path, "logs.txt")
            if os.path.exists(log_file):
                try:
                    with open(log_file, 'r', errors='ignore') as f:
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(max(size - 3000, 0))
                        logs = f.read()
                except: pass
                
            apps.append({"name": app_name, "running": is_running, "logs": logs})
            
    return jsonify({"apps": apps})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

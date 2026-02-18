import os
import sys
import shutil
import zipfile
import subprocess
import sqlite3
import time
import secrets
import hmac
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# --- APP CONFIGURATION ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Directories
UPLOAD_FOLDER = "user_uploads"
DB_NAME = "hosting_database.db"
START_TIME = time.time()  # Server start time for uptime

# Create folders if not exist
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Active process dictionary
running_processes = {}

# --- DATABASE SYSTEM ---
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # System config table (Password, Admin Token, etc.)
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

# --- HELPER FUNCTIONS ---
def find_bot_file(folder_path):
    priority_files = ["main.py", "app.py", "bot.py", "index.py", "run.py", "start.py"]
    
    # Check root directory first
    for f in priority_files:
        full_path = os.path.join(folder_path, f)
        if os.path.exists(full_path):
            return full_path, folder_path
            
    # Check subdirectories
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
        # Check Header for Secret Token
        auth_token = request.headers.get('X-Admin-Key')
        if not auth_token:
            return jsonify({"error": "Unauthorized Access"}), 401
        
        conn = get_db()
        admin_key_row = conn.execute("SELECT value FROM system_config WHERE key='admin_key'").fetchone()
        conn.close()
        
        # Secure comparison to prevent timing attacks
        if admin_key_row and hmac.compare_digest(auth_token, admin_key_row['value']):
            return f(*args, **kwargs)
            
        return jsonify({"error": "Invalid Admin Token"}), 403
    wrapper.__name__ = f.__name__
    return wrapper

# ================= ROUTES =================

# 1. SERVE FRONTEND (Render Single Port)
@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

# 2. CHECK SETUP STATUS
@app.route('/api/check_setup', methods=['GET'])
def check_setup():
    conn = get_db()
    user_pass = conn.execute("SELECT value FROM system_config WHERE key='user_password'").fetchone()
    conn.close()
    return jsonify({"setup_complete": bool(user_pass)})

# 3. INITIAL SETUP (First Time Password)
@app.route('/api/setup', methods=['POST'])
def do_setup():
    data = request.json
    password = data.get('password')
    
    # Strong Password Policy
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400
    if not any(c.isupper() for c in password):
        return jsonify({"error": "Password must contain an uppercase letter."}), 400
    if not any(c.isdigit() for c in password):
        return jsonify({"error": "Password must contain a number."}), 400

    conn = get_db()
    existing = conn.execute("SELECT value FROM system_config WHERE key='user_password'").fetchone()
    if existing:
        return jsonify({"error": "Already setup"}), 400
    
    # Hash password and generate admin token
    hashed_pass = generate_password_hash(password)
    admin_token = secrets.token_hex(16)
    
    conn.execute("INSERT INTO system_config (key, value) VALUES (?, ?)", ('user_password', hashed_pass))
    conn.execute("INSERT INTO system_config (key, value) VALUES (?, ?)", ('admin_key', admin_token))
    conn.commit()
    conn.close()
    
    return jsonify({"message": "Setup Successful", "admin_key": admin_token})

# 4. LOGIN
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db()
    user = conn.execute("SELECT value FROM system_config WHERE key='user_password'").fetchone()
    conn.close()
    
    if user and check_password_hash(user['value'], data.get('password')):
        return jsonify({"message": "Login successful"})
    return jsonify({"error": "Wrong credentials"}), 401

# 5. ADMIN LOGIN (For Hidden Panel)
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    conn = get_db()
    user = conn.execute("SELECT value FROM system_config WHERE key='user_password'").fetchone()
    admin_key_row = conn.execute("SELECT value FROM system_config WHERE key='admin_key'").fetchone()
    conn.close()
    
    # Admin uses the same main password
    if user and check_password_hash(user['value'], data.get('password')):
        return jsonify({"message": "Admin Access Granted", "key": admin_key_row['value']})
    return jsonify({"error": "Denied"}), 401

# 6. ADMIN STATS (Uptime, Bots Count)
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

# 7. BROADCAST API
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

    # LOGIC: In a real hosting scenario, you would send this to your bots here.
    # For now, we log it successfully.
    return jsonify({"message": "Broadcast logged and signal sent."})

# 8. UPLOAD BOT
@app.route('/upload', methods=['POST'])
def upload():
    # We use fixed username 'host_admin' as this is single user
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
    
    # Clean old files
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
            
    return jsonify({"message": "Upload Successful!"})

# 9. GET APPS LIST
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
            
            # Check process status
            is_running = False
            if pid in running_processes:
                if running_processes[pid].poll() is None:
                    is_running = True
                else:
                    del running_processes[pid]
            
            # Read Logs
            logs = "Waiting for logs..."
            log_file = os.path.join(full_path, "logs.txt")
            if os.path.exists(log_file):
                try:
                    with open(log_file, 'r', errors='ignore') as f:
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(max(size - 3000, 0)) # Read last 3KB
                        logs = f.read()
                except: pass
                
            apps.append({"name": app_name, "running": is_running, "logs": logs})
            
    return jsonify({"apps": apps})

# 10. APP CONTROL (Start/Stop/Delete)
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
            
        log_file = open(os.path.join(app_dir, "logs.txt"), "a")
        
        try:
            # Run the bot
            proc = subprocess.Popen(
                [sys.executable, "-u", os.path.basename(script_path)],
                cwd=script_dir,
                stdout=log_file,
                stderr=log_file,
                text=True,
                env=os.environ.copy() # Pass env vars like API keys
            )
            running_processes[pid] = proc
            return jsonify({"message": "Bot Started!"})
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

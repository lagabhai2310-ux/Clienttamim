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
START_TIME = time.time()

# --- BROADCAST LISTENER CODE (This will be injected automatically) ---
# এই কোডটি স্বয়ংক্রিয়ভাবে বটের সাথে যুক্ত হবে এবং ব্রডকাস্ট গ্রহণ করবে
BROADCAST_LISTENER_SCRIPT = """
import requests
import threading
import time
import os

# Server URL (Auto-detect or use localhost)
# যদি রিয়েল সার্ভারে থাকে তবে localhost এর জায়গায় আপনার ডোমেইন দিতে পারেন
SERVER_URL = "https://hostpromax.xo.je" 

def check_loop():
    last_id = 0
    while True:
        try:
            # Server এ নতুন মেসেজ আছে কিনা চেক করা
            res = requests.get(f"{SERVER_URL}/api/check_broadcast?last_id={last_id}", timeout=10)
            data = res.json()
            
            if data.get("new_broadcast"):
                msg_text = data.get("content", "")
                img = data.get("image_url")
                btn_name = data.get("button_name")
                btn_url = data.get("button_url")
                
                # এখানে আমরা একটি ফাইলে মেসেজ সেভ করছি যাতে মূল বট কোড এটি পড়তে পারে
                # অথবা আপনি চাইলে এখানে সরাসরি লজিক লিখতে পারেন
                with open("last_broadcast.txt", "w") as f:
                    f.write(f"{msg_text}||{img}||{btn_name}||{btn_url}")
                
                print(f"[System] New Broadcast Received: {msg_text}")
                last_id = data.get("id")
        except Exception as e:
            pass # Error hole skip korbo
            
        time.sleep(5) # Prottek 5 second e check korbe

# Background e start kora
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
    c.execute('''CREATE TABLE IF NOT EXISTS system_config 
                 (key TEXT PRIMARY KEY, value TEXT)''')
    
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
def setup_initial_credentials():
    conn = get_db()
    user_pass = conn.execute("SELECT value FROM system_config WHERE key='user_password'").fetchone()
    
    if not user_pass:
        print(">>> Initializing System with Hardcoded Password...")
        hashed_pass = generate_password_hash("2310")
        admin_token = secrets.token_hex(16)
        
        conn.execute("INSERT INTO system_config (key, value) VALUES (?, ?)", ('user_password', hashed_pass))
        conn.execute("INSERT INTO system_config (key, value) VALUES (?, ?)", ('admin_key', admin_token))
        conn.commit()
        print(">>> Setup Complete. Password is '2310'")
    conn.close()

setup_initial_credentials()

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

@app.route('/api/check_setup', methods=['GET'])
def check_setup():
    return jsonify({"setup_complete": True})

@app.route('/api/setup', methods=['POST'])
def do_setup():
    return jsonify({"error": "System already configured."}), 400

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db()
    user = conn.execute("SELECT value FROM system_config WHERE key='user_password'").fetchone()
    conn.close()
    
    if user and check_password_hash(user['value'], data.get('password')):
        return jsonify({"message": "Login successful"})
    return jsonify({"error": "Wrong credentials"}), 401

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    conn = get_db()
    user = conn.execute("SELECT value FROM system_config WHERE key='user_password'").fetchone()
    admin_key_row = conn.execute("SELECT value FROM system_config WHERE key='admin_key'").fetchone()
    conn.close()
    
    if user and check_password_hash(user['value'], data.get('password')):
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

# --- BROADCAST API (FIXED) ---
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
    new_secret = secrets.token_hex(8)
    conn.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('broadcast_secret', ?)", (new_secret,))
    conn.commit()
    conn.close()

    return jsonify({"message": "Broadcast sent successfully!", "new_secret": new_secret})

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

    # --- AUTO INJECTION SYSTEM ---
    # এখানে আমরা বটের ফোল্ডারে লিসেনার ফাইল তৈরি করছি
    listener_path = os.path.join(user_dir, "auto_listener.py")
    with open(listener_path, "w") as f:
        f.write(BROADCAST_LISTENER_SCRIPT)
    print(f"Injected broadcast listener into {app_name}")
            
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
        
        # মূল বট ফাইলের নাম নিই
        main_script_name = os.path.basename(script_path)
        
        # আমরা একটি wrapper স্ক্রিপ্ট তৈরি করব যেটি আগে লিসেনার লোড করবে, তারপর বট
        wrapper_path = os.path.join(script_dir, "run_wrapper.py")
        
        # Wrapper কোড জেনারেট করা (লিসেনার ইম্পোর্ট + মূল বট রান)
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
# We use exec to ensure it runs as the main file
exec(open('{main_script_name}').read())
"""
        # Wrapper ফাইল সেভ করা
        with open(wrapper_path, "w") as f:
            f.write(wrapper_code)

        log_file = open(os.path.join(app_dir, "logs.txt"), "a")
        
        try:
            # এখন মূল বটের বদলে wrapper রান করাবো
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

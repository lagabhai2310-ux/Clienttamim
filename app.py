# --- START OF FILE app.py ---

import os
import sys
import shutil
import zipfile
import subprocess
import sqlite3
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
# CORS Settings
CORS(app, resources={r"/*": {"origins": "*"}})

# --- CONFIGURATION ---
# Render এর Secret Environment Variable থেকে Key নিবে, না থাকলে ডিফল্ট
app.secret_key = os.environ.get("SECRET_KEY", "super_secret_laga_key_v5")
UPLOAD_FOLDER = "user_uploads"

# Render-এ Data Persistence এর জন্য স্মার্ট Path Logic
# Render সাধারণত /tmp ফোল্ডার ক্লিয়ার করে দেয়, তাই আমরা বর্তমান ডিরেক্টরি ব্যবহার করব
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = os.path.join(BASE_DIR, "lagahost.db")

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

running_processes = {}

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT)''')
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# --- SMART FILE FINDER ---
def find_bot_file(folder_path):
    priority_files = ["main.py", "app.py", "bot.py", "index.py", "start.py", "run.py"]
    
    for f in priority_files:
        full_path = os.path.join(folder_path, f)
        if os.path.exists(full_path):
            return full_path, folder_path
            
    for root, dirs, files in os.walk(folder_path):
        if "__pycache__" in root or ".git" in root: continue
        for f in priority_files:
            if f in files:
                return os.path.join(root, f), root
        for f in files:
            if f.endswith(".py"):
                return os.path.join(root, f), root
                
    return None, None

# --- API ROUTES ---

@app.route('/')
def home():
    return jsonify({
        "status": "Running",
        "message": "LagaHost V5 Backend is Live!",
        "docs": "Ready for Telegram Bots"
    })

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    u = data.get('username')
    p = data.get('password')
    if not u or not p: return jsonify({"error": "Missing fields"}), 400
    
    try:
        conn = get_db()
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", 
                     (u, generate_password_hash(p)))
        conn.commit()
        return jsonify({"message": "Success"})
    except:
        return jsonify({"error": "Username taken"}), 409
    finally:
        conn.close()

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (data.get('username'),)).fetchone()
    conn.close()
    
    if user and check_password_hash(user['password'], data.get('password')):
        return jsonify({"message": "Login successful", "username": user['username']})
    return jsonify({"error": "Wrong credentials"}), 401

@app.route('/upload', methods=['POST'])
def upload():
    username = request.form.get('username')
    if not username: return jsonify({"error": "No username provided"}), 400
    
    if 'file' not in request.files:
        return jsonify({"error": "No file sent"}), 400
        
    file = request.files['file']
    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in ['.zip', '.py']:
        return jsonify({"error": f"Invalid file type: {ext}. Only .zip or .py allowed!"}), 400
        
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
            
            # --- AUTO REQUIREMENTS INSTALLER ---
            req_path = None
            for root, dirs, files in os.walk(user_dir):
                if "requirements.txt" in files:
                    req_path = os.path.join(root, "requirements.txt")
                    break
            
            if req_path:
                print(f"Installing requirements for {app_name}...")
                # User folder এ install না করে Global env এ install করবে (Render এ এভাবেই কাজ করে)
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], check=False)
                             
        except Exception as e:
            return jsonify({"error": f"Zip failed: {str(e)}"}), 500
            
    return jsonify({"message": "Upload & Setup Successful!"})

@app.route('/my_apps', methods=['POST'])
def my_apps():
    username = request.json.get('username')
    user_path = os.path.join(UPLOAD_FOLDER, username)
    
    if not os.path.exists(user_path): return jsonify({"apps": []})
    
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
                        f.seek(max(size - 4000, 0))
                        logs = f.read()
                except: pass
                
            apps.append({"name": app_name, "running": is_running, "logs": logs})
            
    return jsonify({"apps": apps})

@app.route('/action', methods=['POST'])
def action():
    data = request.json
    act = data.get('action')
    usr = data.get('username')
    app_name = data.get('app_name')
    
    pid = f"{usr}_{app_name}"
    app_dir = os.path.join(UPLOAD_FOLDER, usr, app_name)
    
    if act == "start":
        if pid in running_processes and running_processes[pid].poll() is None:
            return jsonify({"message": "Already Running!"})
            
        script_path, script_dir = find_bot_file(app_dir)
        
        if not script_path:
            return jsonify({"error": "No python file found!"}), 404
            
        log_file = open(os.path.join(app_dir, "logs.txt"), "a")
        
        try:
            # Environment Variables পাস করা হচ্ছে (যেমন BOT_TOKEN)
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
            return jsonify({"message": "Bot Started Successfully!"})
        except Exception as e:
            return jsonify({"error": f"Start failed: {str(e)}"}), 500

    elif act == "stop":
        if pid in running_processes:
            p = running_processes[pid]
            p.terminate()
            try: p.wait(timeout=2)
            except: p.kill()
            del running_processes[pid]
            return jsonify({"message": "Stopped!"})
        return jsonify({"error": "Not running"})

    elif act == "delete":
        if pid in running_processes:
            try:
                running_processes[pid].kill()
                del running_processes[pid]
            except: pass
            
        if os.path.exists(app_dir):
            shutil.rmtree(app_dir)
            return jsonify({"message": "Deleted!"})
        return jsonify({"error": "Not found"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

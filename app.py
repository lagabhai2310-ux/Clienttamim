# ================================
# HOSTPY PRO BACKEND — FULL BUILD
# Flask + SQLite + Telebot
# Multi-User Bot Hosting System
# ================================

import os
import sys
import shutil
import zipfile
import subprocess
import sqlite3
import time
import re
import threading
import telebot
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# ================= CONFIG =================

app = Flask(__name__)
CORS(app)

SECRET_KEY = os.environ.get("SECRET_KEY", "hostpy_super_secret")
UPLOAD_FOLDER = "user_uploads"
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = os.path.join(BASE_DIR, "hostpy.db")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

running_processes = {}
server_start_time = time.time()

# ================= DATABASE =================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        bot_token TEXT,
        chat_id TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ================= UTILITIES =================

def extract_token_from_code(path):
    """Detect Telegram Bot Token from Python code"""
    try:
        with open(path, "r", errors="ignore") as f:
            content = f.read()

        pattern = r'\b\d{9,10}:[A-Za-z0-9_-]{30,40}\b'
        match = re.search(pattern, content)

        if match:
            return match.group(0)

    except Exception as e:
        print("Token extraction error:", e)

    return None


def find_main_py(folder):
    """Find main bot file"""
    priority = ["main.py", "app.py", "bot.py", "run.py", "start.py"]

    for f in priority:
        p = os.path.join(folder, f)
        if os.path.exists(p):
            return p, folder

    for root, _, files in os.walk(folder):
        for f in files:
            if f.endswith(".py"):
                return os.path.join(root, f), root

    return None, None

# ================= CHAT ID COLLECTOR =================

def collect_chat_id(username, token):
    """
    Try up to 60 sec to capture chat_id
    Requires user to send /start
    """

    bot = telebot.TeleBot(token)

    for _ in range(12):  # 12 × 5 sec = ~60 sec
        try:
            updates = bot.get_updates(limit=1, timeout=10)

            if updates:
                upd = updates[0]
                if upd.message:
                    chat_id = str(upd.message.chat.id)

                    conn = get_db()
                    conn.execute(
                        "UPDATE users SET chat_id=? WHERE username=?",
                        (chat_id, username)
                    )
                    conn.commit()
                    conn.close()

                    print(f"[CHAT SAVED] {username} -> {chat_id}")
                    return

        except Exception as e:
            print("ChatID error:", e)

        time.sleep(5)

    print(f"[NO CHAT] {username} did not start bot")

# ================= HOME =================

@app.route("/")
def home():
    return jsonify({
        "status": "Hostpy Backend Running",
        "uptime": int(time.time() - server_start_time)
    })

# ================= REGISTER =================

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    u = data.get("username")
    p = data.get("password")

    if not u or not p:
        return jsonify({"error": "Missing fields"}), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username,password,bot_token,chat_id) VALUES (?,?,?,?)",
            (u, generate_password_hash(p), "", "")
        )
        conn.commit()
        return jsonify({"message": "Registered"})
    except:
        return jsonify({"error": "Username exists"}), 409
    finally:
        conn.close()

# ================= LOGIN =================

@app.route("/login", methods=["POST"])
def login():
    data = request.json

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=?",
        (data.get("username"),)
    ).fetchone()
    conn.close()

    if user and check_password_hash(user["password"], data.get("password")):
        return jsonify({"message": "Login success"})

    return jsonify({"error": "Invalid credentials"}), 401

# ================= UPLOAD BOT =================

@app.route("/upload", methods=["POST"])
def upload():
    username = request.form.get("username")
    file = request.files.get("file")

    if not username or not file:
        return jsonify({"error": "Missing data"}), 400

    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()

    if ext not in [".zip", ".py"]:
        return jsonify({"error": "Invalid file"}), 400

    app_name = os.path.splitext(filename)[0]
    user_dir = os.path.join(UPLOAD_FOLDER, username, app_name)

    shutil.rmtree(user_dir, ignore_errors=True)
    os.makedirs(user_dir, exist_ok=True)

    save_path = os.path.join(user_dir, filename)
    file.save(save_path)

    token = None

    if ext == ".zip":
        with zipfile.ZipFile(save_path, "r") as z:
            z.extractall(user_dir)

        os.remove(save_path)

        main_file, _ = find_main_py(user_dir)
        if main_file:
            token = extract_token_from_code(main_file)

    else:
        token = extract_token_from_code(save_path)

    if token:
        conn = get_db()
        conn.execute(
            "UPDATE users SET bot_token=? WHERE username=?",
            (token, username)
        )
        conn.commit()
        conn.close()

    return jsonify({"message": "Upload success", "token_found": bool(token)})

# ================= LIST APPS =================

@app.route("/my_apps", methods=["POST"])
def my_apps():
    username = request.json.get("username")
    user_path = os.path.join(UPLOAD_FOLDER, username)

    if not os.path.exists(user_path):
        return jsonify({"apps": []})

    apps = []

    for app_name in os.listdir(user_path):
        full = os.path.join(user_path, app_name)
        if os.path.isdir(full):

            pid = f"{username}_{app_name}"
            running = pid in running_processes and running_processes[pid].poll() is None

            log_file = os.path.join(full, "logs.txt")
            logs = ""

            if os.path.exists(log_file):
                with open(log_file, "r", errors="ignore") as f:
                    logs = f.read()[-3000:]

            apps.append({
                "name": app_name,
                "running": running,
                "logs": logs
            })

    return jsonify({"apps": apps})

# ================= ACTION =================

@app.route("/action", methods=["POST"])
def action():
    data = request.json
    act = data.get("action")
    username = data.get("username")
    app_name = data.get("app_name")

    pid = f"{username}_{app_name}"
    app_dir = os.path.join(UPLOAD_FOLDER, username, app_name)

    # ---------- START ----------
    if act == "start":

        if pid in running_processes and running_processes[pid].poll() is None:
            return jsonify({"message": "Already running"})

        script, cwd = find_main_py(app_dir)

        if not script:
            return jsonify({"error": "No python file"}), 404

        log = open(os.path.join(app_dir, "logs.txt"), "a")

        proc = subprocess.Popen(
            [sys.executable, "-u", os.path.basename(script)],
            cwd=cwd,
            stdout=log,
            stderr=log,
            text=True
        )

        running_processes[pid] = proc

        token = extract_token_from_code(script)

        if token:
            threading.Thread(
                target=collect_chat_id,
                args=(username, token),
                daemon=True
            ).start()

        return jsonify({"message": "Bot started"})

    # ---------- STOP ----------
    if act == "stop":
        if pid in running_processes:
            running_processes[pid].terminate()
            del running_processes[pid]
            return jsonify({"message": "Stopped"})
        return jsonify({"error": "Not running"})

    # ---------- DELETE ----------
    if act == "delete":
        if pid in running_processes:
            running_processes[pid].kill()
            del running_processes[pid]

        shutil.rmtree(app_dir, ignore_errors=True)
        return jsonify({"message": "Deleted"})

    return jsonify({"error": "Invalid action"}), 400

# ================= BROADCAST =================

@app.route("/broadcast", methods=["POST"])
def broadcast():
    data = request.json

    if data.get("admin_key") != "PROTECTED_BROADCAST_KEY":
        return jsonify({"error": "Unauthorized"}), 403

    msg = data.get("message")
    img = data.get("image_url")
    btn_name = data.get("button_name")
    btn_url = data.get("button_url")

    if not msg:
        return jsonify({"error": "Message empty"}), 400

    conn = get_db()
    users = conn.execute(
        "SELECT bot_token, chat_id FROM users"
    ).fetchall()
    conn.close()

    sent = 0

    def send(token, chat_id):
        nonlocal sent
        try:
            bot = telebot.TeleBot(token)

            markup = None
            if btn_name and btn_url:
                markup = telebot.types.InlineKeyboardMarkup()
                markup.add(
                    telebot.types.InlineKeyboardButton(btn_name, url=btn_url)
                )

            if img:
                bot.send_photo(chat_id, img, caption=msg, reply_markup=markup)
            else:
                bot.send_message(chat_id, msg, reply_markup=markup)

            sent += 1
            print("Sent to", chat_id)

        except Exception as e:
            print("Failed:", chat_id, e)

    threads = []

    for u in users:
        if u["bot_token"] and u["chat_id"]:
            t = threading.Thread(
                target=send,
                args=(u["bot_token"], u["chat_id"])
            )
            t.start()
            threads.append(t)
            time.sleep(0.05)

    return jsonify({
        "status": "Broadcast started",
        "targets": len(threads)
    })

# ================= SERVER STATS =================

@app.route("/server_stats")
def stats():
    active = sum(p.poll() is None for p in running_processes.values())

    return jsonify({
        "uptime": int(time.time() - server_start_time),
        "active_bots": active
    })

# ================= RUN =================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

import os
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, has_request_context, current_app
from werkzeug.utils import secure_filename
import json
from functools import wraps
import threading
import telebot
from datetime import datetime, timedelta, UTC
from pathlib import Path
import time
import _thread
import inspect
import traceback

app = Flask(__name__)
lock = threading.Lock()

CURRENT_VERSION = "1.0"
UPLOAD_FOLDER = 'static'
BASE_DIR = Path(__file__).resolve().parent
HEARTBEAT_TIMEOUT = timedelta(seconds=30)

bot_token = ''
bot_admins = []

user_heartbeats = {}

# --- LOGGING ---
log = []
LOG_FILE = BASE_DIR / "main_log.txt"
_log_lock = threading.Lock()

def _log(message: str):
    global log
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = message.split('\n')
    with _log_lock:
        for msg in lines:
            entry = f"[main.py] - [{ts}]: {msg}"
            log.append(entry)
            print(f"[main.py]: {msg}")
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                for msg in lines:
                    f.write(f"[main.py] - [{ts}]: {msg}\n")
        except Exception as e:
            print(f"[main.py]: Failed to write log file: {e}")

def _clear_logs():
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("")

@app.before_request
def _log_each_request():
    try:
        _log(f"<- request: {request.method} {request.path} | ip={request.remote_addr} | ua={request.headers.get('User-Agent','')}")
    except Exception:
        pass

@app.after_request
def _log_each_response(response):
    try:
        _log(f"-> response: {request.method} {request.path} -> {response.status_code}")
    except Exception:
        pass
    return response
# --- /LOGGING ---

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists('stats.json'):
    with open('stats.json', 'w', encoding='utf-8') as f:
        json.dump({"open": 0, "api": 0, "all": 0, "image": 0}, f, ensure_ascii=False, indent=2)

def _get_caller_name() -> str:
    # 1) если мы внутри Flask запроса, берём эндпоинт
    ep = _get_flask_endpoint_name()
    if ep:
        return ep

    # 2) иначе fallback на inspect (для фоновых задач/скриптов)
    frame = inspect.currentframe()
    try:
        f = frame.f_back.f_back if frame else None
        return f.f_code.co_name if f else "<unknown>"
    finally:
        del frame

def _get_flask_endpoint_name() -> str | None:
    if not has_request_context():
        return None

    ep = request.endpoint
    if not ep:
        return None

    view = current_app.view_functions.get(ep)
    return view.__name__ if view else ep

def ensure_aware_utc(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)

def load(fname: str):
    caller = _get_caller_name()
    with lock:
        try:
            with open(fname, 'r', encoding='utf-8') as f:
                _log(f"json loaded -> {fname} | caller is {caller}")
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            _log(f"error with json load -> {fname} | exception is ({type(e).__name__}) | caller is {caller}")
            return []
        except Exception as e:
            _log(f"error with json load -> {fname} | exception is ({type(e).__name__}) | caller is {caller}")
            _log(traceback.format_exc())
            raise
            # (идем крашить нахуй скрипт, мало ли че загрузить пытаемся)


def save(fname: str, data):
    caller = _get_caller_name()
    with lock:
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            _log(f"json saved <- {fname} | caller is {caller}")

def stats(api: bool = False, image: bool = False):
    statistics = load('stats.json')

    if api:
        statistics['api'] = statistics.get('api') + 1
    elif image:
        statistics['image'] = statistics.get('image') + 1
    else:
        statistics['open'] = statistics.get('open') + 1
    statistics['all'] = statistics.get('all') + 1

    save('stats.json', statistics)

def require_can_edit(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        can_edit = request.cookies.get("can_edit", "")
        if str(can_edit).lower() != "true":
            return render_template('index.html'), 401
        return fn(*args, **kwargs)
    return wrapper

def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        can_edit = request.cookies.get("admin", "")
        settings = load('settings.json')
        if str(can_edit).lower() != settings['admin_cookie']:
            return render_template('index.html'), 401
        return fn(*args, **kwargs)
    return wrapper

@app.route('/restart')
@require_admin
def server_restart():
    _log("call /restart")
    def close():
        _log("checking start updater.py with server stop in settings.json...")

        settings = load('settings.json')
        if settings['reload_with_updater']:
            os.startfile('updater.py')
            _log("reload_with_updater is true -> stopping and update...")
        else:
            _log("reload_with_updater is false")
        
        time.sleep(0.2)

        _thread.interrupt_main()
        
    threading.Thread(target=close, daemon=True).start()
    return "restarting..."

@app.route('/kill')
@require_admin
def sever_kill():
    _log("call /kill")
    os._exit(0)
    return "killed"

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    data = request.json or {}
    user_id = data.get("id")

    if not user_id:
        _log("HEARTBEAT missing user_id")
        return jsonify({"error": "user_id required"}), 400

    user_heartbeats[user_id] = datetime.utcnow()
    return jsonify({"status": "ok"})

@app.route("/users/status")
@require_admin
def users_status():
    now = datetime.now(UTC)

    result = []
    for user_id, last_seen in user_heartbeats.items():
        last_seen = ensure_aware_utc(last_seen)

        online = (now - last_seen) < HEARTBEAT_TIMEOUT

        result.append({
            "user_id": user_id,
            "online": online,
            "last_seen": last_seen.isoformat()
        })

    return jsonify(result)


@app.route("/system-files", methods=["GET"])
@require_admin
def list_files():
    base_dir = Path(__file__).resolve().parent

    allowed_ext = {".json", ".txt"}

    files = sorted([
        p.name
        for p in base_dir.iterdir()
        if p.is_file() and p.suffix.lower() in allowed_ext
    ])

    return jsonify({
        "directory": str(base_dir),
        "count": len(files),
        "files": files
    })

@app.route("/json/<filename>", methods=["GET"])
@require_admin
def get_file(filename):
    allowed_ext = {".json", ".txt"}

    file_path = (BASE_DIR / filename).resolve()

    # защита от ../
    if BASE_DIR not in file_path.parents:
        return jsonify({"error": "invalid path"}), 403

    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "file not found"}), 404

    if file_path.suffix.lower() not in allowed_ext:
        return jsonify({"error": "only .json and .txt allowed"}), 400

    try:
        if file_path.suffix.lower() == ".json":
            data = load(file_path)
            return jsonify(data)

        # .txt
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        return Response(text, mimetype="text/plain; charset=utf-8")

    except json.JSONDecodeError:
        return jsonify({"error": "invalid json file"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/json/<filename>", methods=["POST"])
@require_admin
def write_json_file(filename):
    if not filename.endswith(".json"):
        return jsonify({"error": "only .json files allowed"}), 400

    file_path = (BASE_DIR / filename).resolve()

    if BASE_DIR not in file_path.parents:
        return jsonify({"error": "invalid path"}), 403

    if not request.is_json:
        return jsonify({"error": "request body must be json"}), 400

    data = request.get_json()

    try:
        save(file_path, data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "saved", "file": filename})

@app.route('/all')
def all():
    stats(api=True)
    return jsonify(load('all.json')), 200

@app.route('/get')
def get():
    stats(api=True)
    faq = request.args.get('faq', '')
    if faq:
        data = load('faq.json')
        if faq in data:
            return jsonify(data[faq]), 200
        else:
            return jsonify({"error": "FAQ not found"}), 404
    return jsonify({"error": "No FAQ specified"}), 400

@app.route('/edit', methods=['POST'])
@require_can_edit
def edit():
    data = request.get_json()
    faq_id = data.get('id', '').strip()

    titles = data.get('titles', {})
    contents = data.get('contents', {})
    tags = data.get('tags', [])

    if not faq_id or not titles.get('ru'):
        return jsonify({"error": "Missing ID or RU title"}), 400

    for lang in ['en', 'ro']:
        if not titles.get(lang):
            titles[lang] = titles.get('ru')
        if not contents.get(lang):
            contents[lang] = contents.get('ru')

    all_faqs = load('all.json')
    faq_data = load('faq.json')

    faq_data[faq_id] = {
        "id": faq_id,
        "titles": titles,
        "contents": contents,
        "tags": tags
    }

    found = False
    for item in all_faqs:
        if item.get('id') == faq_id:
            item['titles'] = titles
            item['tags'] = tags
            found = True
            break

    if not found:
        all_faqs.append({
            "id": faq_id,
            "titles": titles,
            "tags": tags
        })

    save('all.json', all_faqs)
    save('faq.json', faq_data)

    return jsonify({"status": "success"}), 201

@app.route('/delete', methods=['POST'])
@require_can_edit
def delete():
    data = request.get_json()
    faq_id = data.get('id', '')
    if not faq_id:
        return jsonify({"error": "No FAQ ID provided"}), 400

    all_faqs = load('all.json')
    faq_data = load('faq.json')

    all_faqs = [faq for faq in all_faqs if faq['id'] != faq_id]
    if faq_id in faq_data:
        del faq_data[faq_id]

    save('all.json', all_faqs)
    save('faq.json', faq_data)

    return jsonify({"status": "deleted"}), 200

@app.route('/upload', methods=['POST'])
@require_can_edit
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        custom_name = request.form.get('custom_name')
        if custom_name:
            original_ext = os.path.splitext(file.filename)[1].lower()
            if not custom_name.lower().endswith(original_ext):
                custom_name += original_ext
            filename = secure_filename(custom_name)
        else:
            filename = secure_filename(file.filename)

        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            return jsonify({'error': 'File already exists', 'code': 'FILE_EXISTS', 'current_name': filename}), 409

        file.save(file_path)
        return jsonify({'filename': filename}), 200

@app.route('/assets/<path:filename>')
def assets(filename):
    stats(image=True)
    return send_from_directory('static', filename)

@app.route('/')
def index():
    stats()
    return render_template('index.html')

@app.route('/edit_page')
@require_can_edit
def edit_page():
    return render_template('edit.html')

@app.route('/info')
@require_admin
def info():
    return render_template('info.html')

@app.route('/list_assets')
def list_assets():
    try:
        files = os.listdir(app.config['UPLOAD_FOLDER'])
        images = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'))]
        return jsonify(images), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/problem')
def problem():
    name = request.args.get('name', '')
    problem = request.args.get('desc', '')

    if not name or not problem:
        return {'error': 'name or problem is empty!'}, 400
    
    bot = telebot.TeleBot(bot_token, parse_mode="HTML")
    for admin in bot_admins:
        bot.send_message(admin, f'Отзыв от <b>{name}</b>.\n\nПроблема:\n{problem}')

    return {"message": "ok"}, 201


if __name__ == '__main__':
    _clear_logs()

    _log("checking settings.json...")
    settings = load('settings.json')
    settings["current_ver"] = CURRENT_VERSION
    bot_token = settings["bot_token"]
    bot_admins = settings["bot_admins"]
    save('settings.json', settings)
    _log("settings.json is ok")

    _log("server starting...")
    try:
        app.run(debug=False, host='0.0.0.0', port=settings.get('port', 0), use_reloader=False)
    except Exception:
        _log("server stopping after fatal error...")
        os._exit(0)
    finally:
        _log("server stopping...")

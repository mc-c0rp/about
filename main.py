import os
from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename
import json
from functools import wraps

app = Flask(__name__)

UPLOAD_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def load(fname: str):
    try:
        with open(fname, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return [] if fname == 'all.json' else {}

def save(fname: str, data):
    with open(fname, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def require_can_edit(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        can_edit = request.cookies.get("can_edit", "")
        if str(can_edit).lower() != "true":
            return render_template('index.html'), 401
        return fn(*args, **kwargs)
    return wrapper

@app.route('/all')
def all():
    return jsonify(load('all.json')), 200

@app.route('/get')
def get():
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
    return send_from_directory('static', filename)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/edit_page')
@require_can_edit
def edit_page():
    return render_template('edit.html')

@app.route('/list_assets')
def list_assets():
    try:
        files = os.listdir(app.config['UPLOAD_FOLDER'])
        images = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'))]
        return jsonify(images), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)

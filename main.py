import os
import glob
import subprocess
import json
import threading
import uuid
import time
import random
import urllib.request
import urllib.parse
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from PIL import Image

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'outputs')
HASIL_VIDEO_FOLDER = os.path.join(BASE_DIR, 'hasil_video')
PIN_HISTORY_FILE = os.path.join(BASE_DIR, 'pin_history.json')

# ─── WEB 1 CONFIG ─────────────────────────────────────────
# URL web 1 (bisa di-set via environment variable atau langsung di sini)
WEB1_URL = os.environ.get('WEB1_URL', 'https://small-jeana-botalesya-7f9a1b98.koyeb.app')
WEB1_API_KEY = os.environ.get('WEB1_API_KEY', '')  # kosong = tidak pakai API key
SENT_LOG_FILE = os.path.join(BASE_DIR, 'sent_log.json')  # track video yg sudah di-send

def get_music_folder():
    candidates = [
        os.path.join(BASE_DIR, 'musik'),
        os.path.join(BASE_DIR, 'music'),
        os.path.expanduser('~/Music'),
        os.path.expanduser('~/Musik'),
        os.path.expanduser('~/music'),
        os.path.expanduser('~/musik'),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    folder = os.path.join(BASE_DIR, 'musik')
    os.makedirs(folder, exist_ok=True)
    return folder

MUSIC_EXTENSIONS = ['.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg']

progress_store = {}


def get_music_duration(music_path):
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'json',
            music_path
        ], capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        duration = float(data['format']['duration'])
        return duration
    except Exception as e:
        print(f"ffprobe error: {e}")
        return None


def get_image_size(image_path):
    with Image.open(image_path) as img:
        return img.size


def make_1080p_size(width, height):
    if width >= height:
        new_h = 1080
        new_w = int(width * 1080 / height)
    else:
        new_w = 1080
        new_h = int(height * 1080 / width)
    new_w = new_w if new_w % 2 == 0 else new_w + 1
    new_h = new_h if new_h % 2 == 0 else new_h + 1
    return new_w, new_h


def create_video_task(task_id, image_path, music_path, output_path):
    try:
        progress_store[task_id] = {'status': 'processing', 'progress': 0, 'message': 'Memulai proses...'}

        progress_store[task_id]['message'] = 'Membaca durasi musik...'
        progress_store[task_id]['progress'] = 10
        duration = get_music_duration(music_path)

        if duration is None or duration <= 0:
            progress_store[task_id] = {'status': 'error', 'message': 'Gagal membaca durasi musik.'}
            return

        progress_store[task_id]['message'] = f'Durasi musik: {int(duration//60)}:{int(duration%60):02d}'
        progress_store[task_id]['progress'] = 20

        w, h = get_image_size(image_path)
        new_w, new_h = make_1080p_size(w, h)

        progress_store[task_id]['message'] = f'Mengatur resolusi: {new_w}x{new_h}...'
        progress_store[task_id]['progress'] = 30

        music_name = os.path.basename(music_path)
        progress_store[task_id]['message'] = f'Membuat video dengan: {music_name}'
        progress_store[task_id]['progress'] = 40

        cmd = [
            'ffmpeg', '-y',
            '-loop', '1',
            '-i', image_path,
            '-i', music_path,
            '-vf', f'scale={new_w}:{new_h}:flags=lanczos',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '18',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-t', str(duration),
            '-shortest',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            output_path
        ]

        progress_store[task_id]['progress'] = 50
        progress_store[task_id]['message'] = 'Encoding video...'

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        start_time = time.time()
        estimated_duration = max(duration * 0.5, 5)

        while proc.poll() is None:
            elapsed = time.time() - start_time
            enc_progress = min(45, int((elapsed / estimated_duration) * 45))
            progress_store[task_id]['progress'] = 50 + enc_progress
            time.sleep(0.5)

        proc.wait()

        if proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr else ''
            progress_store[task_id] = {
                'status': 'error',
                'message': f'FFmpeg error: {stderr[-300:] if stderr else "unknown"}'
            }
            return

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            progress_store[task_id] = {'status': 'error', 'message': 'File video tidak terbuat.'}
            return

        output_filename = os.path.basename(output_path)
        progress_store[task_id] = {
            'status': 'done',
            'progress': 100,
            'message': 'Video berhasil dibuat!',
            'output_filename': output_filename,
            'music_name': music_name,
            'resolution': f'{new_w}x{new_h}',
            'duration': int(duration)
        }

        # Simpan ke maker_log
        log_entry = {
            'id': task_id,
            'filename': output_filename,
            'music': music_name,
            'resolution': f'{new_w}x{new_h}',
            'duration': int(duration),
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        ml = load_maker_log()
        ml.insert(0, log_entry)
        save_maker_log(ml)

        # Simpan juga ke hasil_video/log.json supaya bisa di-send ke web 1
        hasil_log_entry = {
            'id': task_id,
            'title': output_filename,
            'thumb_url': '',
            'filename': output_filename,
            'music': music_name,
            'resolution': f'{new_w}x{new_h}',
            'duration': int(duration),
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'maker'
        }
        hasil_log_path = os.path.join(HASIL_VIDEO_FOLDER, 'log.json')
        hasil_log = []
        if os.path.exists(hasil_log_path):
            try:
                with open(hasil_log_path) as f:
                    hasil_log = json.load(f)
            except:
                hasil_log = []
        hasil_log.insert(0, hasil_log_entry)
        with open(hasil_log_path, 'w') as f:
            json.dump(hasil_log, f, indent=2)

    except Exception as e:
        progress_store[task_id] = {'status': 'error', 'message': str(e)}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    if 'photo' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['photo']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.webp', '.bmp']:
        return jsonify({'error': 'Format tidak didukung'}), 400
    filename = f"{uuid.uuid4()}{ext}"
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)
    w, h = get_image_size(save_path)
    return jsonify({'filename': filename, 'width': w, 'height': h})


@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/outputs/<filename>")
def serve_output(filename):
    # Cari di hasil_video dulu, fallback ke outputs
    path = os.path.join(HASIL_VIDEO_FOLDER, filename)
    if os.path.exists(path):
        return send_from_directory(HASIL_VIDEO_FOLDER, filename)
    return send_from_directory(OUTPUT_FOLDER, filename)


@app.route('/music-list')
def music_list():
    """Return list of all music files."""
    music_folder = get_music_folder()
    music_files = []
    for ext in MUSIC_EXTENSIONS:
        music_files.extend(glob.glob(os.path.join(music_folder, f'*{ext}')))
        music_files.extend(glob.glob(os.path.join(music_folder, f'*{ext.upper()}')))

    music_files = sorted(set(music_files))

    result = []
    for f in music_files:
        name = os.path.basename(f)
        dur = get_music_duration(f)
        dur_str = ''
        if dur:
            m = int(dur // 60)
            s = int(dur % 60)
            dur_str = f'{m}:{s:02d}'
        result.append({'name': name, 'duration': dur_str})

    return jsonify({'folder': music_folder, 'files': result})


@app.route('/music/<filename>')
def serve_music(filename):
    """Serve a music file for preview."""
    music_folder = get_music_folder()
    return send_from_directory(music_folder, filename)


@app.route('/create', methods=['POST'])
def create():
    data = request.json
    image_filename = data.get('image_filename')
    music_filename = data.get('music_filename')

    if not image_filename:
        return jsonify({'error': 'No image'}), 400
    if not music_filename:
        return jsonify({'error': 'Pilih musik terlebih dahulu'}), 400

    image_path = os.path.join(UPLOAD_FOLDER, image_filename)
    if not os.path.exists(image_path):
        return jsonify({'error': 'Image not found'}), 404

    music_folder = get_music_folder()
    music_path = os.path.join(music_folder, music_filename)
    if not os.path.exists(music_path):
        return jsonify({'error': f'File musik tidak ditemukan: {music_filename}'}), 400

    task_id = str(uuid.uuid4())
    output_filename = f"video_{task_id}.mp4"
    output_path = os.path.join(HASIL_VIDEO_FOLDER, output_filename)

    thread = threading.Thread(
        target=create_video_task,
        args=(task_id, image_path, music_path, output_path)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id})


@app.route('/progress/<task_id>')
def get_progress(task_id):
    data = progress_store.get(task_id, {'status': 'pending', 'progress': 0})
    return jsonify(data)


@app.route("/download/<filename>")
def download(filename):
    # Cari di hasil_video dulu, fallback ke outputs
    path = os.path.join(HASIL_VIDEO_FOLDER, filename)
    if not os.path.exists(path):
        path = os.path.join(OUTPUT_FOLDER, filename)
    if not os.path.exists(path):
        return "File not found", 404
    return send_file(path, as_attachment=True, download_name=filename)


## ─── PIN HISTORY HELPERS ───

def load_pin_history():
    if os.path.exists(PIN_HISTORY_FILE):
        try:
            with open(PIN_HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_pin_history(data):
    with open(PIN_HISTORY_FILE, 'w') as f:
        json.dump(data, f, indent=2)


@app.route('/pinterest/search')
def pinterest_search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'Query kosong'}), 400

    # Kumpulkan URL foto yang sudah dijadikan video (filter utama)
    log_path = os.path.join(HASIL_VIDEO_FOLDER, 'log.json')
    used_thumb_urls = set()
    if os.path.exists(log_path):
        try:
            with open(log_path) as f:
                log_data = json.load(f)
            used_thumb_urls = set(e.get('thumb_url', '') for e in log_data if e.get('thumb_url'))
        except:
            pass

    # seen_ids untuk rotasi tampilan (bukan filter keras)
    history = load_pin_history()
    seen_ids = set(history.get(q, []))

    encoded_q = urllib.parse.quote(q)
    url = f"https://api.nexray.web.id/search/pinterest?q={encoded_q}"

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode('utf-8')
        data = json.loads(raw)
    except Exception as e:
        return jsonify({'error': f'Gagal menghubungi API: {str(e)}'}), 500

    if not data.get('status') or not data.get('result'):
        return jsonify({'error': 'Tidak ada hasil dari Pinterest'}), 404

    results = data['result']

    # Filter keras: buang yang sudah dijadikan video
    not_used = [r for r in results if r.get('images_url', '') not in used_thumb_urls]

    # Dari yang belum dijadikan video, utamakan yang belum pernah tampil
    not_seen = [r for r in not_used if r.get('id') not in seen_ids]

    if len(not_seen) >= 3:
        # Cukup foto baru — tampilkan yang belum pernah tampil
        selected = not_seen[:10]
    elif len(not_used) >= 1:
        # Semua sudah pernah tampil tapi belum dijadikan video — reset rotasi
        history[q] = []
        selected = not_used[:10]
    else:
        # Semua sudah dijadikan video — tampilkan semua (user mungkin mau pilih lagi)
        selected = results[:10]

    # Catat yang ditampilkan ke seen_ids
    new_ids = [r.get('id') for r in selected if r.get('id')]
    all_seen = list(history.get(q, [])) + new_ids
    history[q] = all_seen[-200:]
    save_pin_history(history)

    # Tandai foto yang sudah pernah dijadikan video
    for r in selected:
        r['already_used'] = r.get('images_url', '') in used_thumb_urls

    return jsonify({'status': True, 'result': selected})


@app.route('/pin-make', methods=['POST'])
def pin_make():
    data = request.json
    image_url = data.get('image_url')
    if not image_url:
        return jsonify({'error': 'No image_url'}), 400

    try:
        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.pinterest.com/'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            img_data = resp.read()
    except Exception as e:
        return jsonify({'error': f'Gagal download gambar: {str(e)}'}), 500

    ext = '.jpg'
    if image_url.lower().endswith('.png'):
        ext = '.png'
    elif image_url.lower().endswith('.webp'):
        ext = '.webp'

    img_filename = f"pin_{uuid.uuid4()}{ext}"
    img_path = os.path.join(UPLOAD_FOLDER, img_filename)
    with open(img_path, 'wb') as f:
        f.write(img_data)

    music_folder = get_music_folder()
    music_files = []
    for mext in MUSIC_EXTENSIONS:
        music_files.extend(glob.glob(os.path.join(music_folder, f'*{mext}')))
        music_files.extend(glob.glob(os.path.join(music_folder, f'*{mext.upper()}')))
    music_files = list(set(music_files))

    if not music_files:
        return jsonify({'error': 'Tidak ada file musik. Tambahkan musik ke folder music/'}), 400

    music_path = random.choice(music_files)
    music_name = os.path.basename(music_path)

    task_id = str(uuid.uuid4())
    output_filename = f"pinvid_{task_id}.mp4"
    output_path = os.path.join(HASIL_VIDEO_FOLDER, output_filename)

    pin_title = data.get('title', 'Pinterest Video')

    thread = threading.Thread(
        target=create_pin_video_task,
        args=(task_id, img_path, music_path, output_path, pin_title, image_url)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id})


def create_pin_video_task(task_id, image_path, music_path, output_path, pin_title, thumb_url):
    try:
        progress_store[task_id] = {'status': 'processing', 'progress': 0, 'message': 'Memulai...', 'type': 'pin'}

        progress_store[task_id]['message'] = 'Membaca durasi musik...'
        progress_store[task_id]['progress'] = 10
        duration = get_music_duration(music_path)

        if duration is None or duration <= 0:
            progress_store[task_id] = {'status': 'error', 'message': 'Gagal membaca durasi musik.', 'type': 'pin'}
            return

        progress_store[task_id]['progress'] = 20
        w, h = get_image_size(image_path)
        new_w, new_h = make_1080p_size(w, h)

        progress_store[task_id]['progress'] = 30
        music_name = os.path.basename(music_path)
        progress_store[task_id]['message'] = f'Encoding dengan: {music_name}'
        progress_store[task_id]['progress'] = 40

        cmd = [
            'ffmpeg', '-y',
            '-loop', '1',
            '-i', image_path,
            '-i', music_path,
            '-vf', f'scale={new_w}:{new_h}:flags=lanczos',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '18',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-t', str(duration),
            '-shortest',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            output_path
        ]

        progress_store[task_id]['progress'] = 50
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        start_time = time.time()
        estimated = max(duration * 0.5, 5)
        while proc.poll() is None:
            elapsed = time.time() - start_time
            enc_prog = min(45, int((elapsed / estimated) * 45))
            progress_store[task_id]['progress'] = 50 + enc_prog
            time.sleep(0.5)

        proc.wait()
        if proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr else ''
            progress_store[task_id] = {'status': 'error', 'message': f'FFmpeg error: {stderr[-200:]}', 'type': 'pin'}
            return

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            progress_store[task_id] = {'status': 'error', 'message': 'File video tidak terbuat.', 'type': 'pin'}
            return

        output_filename = os.path.basename(output_path)

        log_entry = {
            'id': task_id,
            'title': pin_title,
            'thumb_url': thumb_url,
            'filename': output_filename,
            'music': music_name,
            'resolution': f'{new_w}x{new_h}',
            'duration': int(duration),
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        log_path = os.path.join(HASIL_VIDEO_FOLDER, 'log.json')
        log = []
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    log = json.load(f)
            except:
                log = []
        log.insert(0, log_entry)
        with open(log_path, 'w') as f:
            json.dump(log, f, indent=2)

        progress_store[task_id] = {
            'status': 'done',
            'progress': 100,
            'message': 'Video selesai!',
            'type': 'pin',
            'output_filename': output_filename,
            'music_name': music_name,
            'resolution': f'{new_w}x{new_h}',
            'duration': int(duration),
            'title': pin_title
        }

    except Exception as e:
        progress_store[task_id] = {'status': 'error', 'message': str(e), 'type': 'pin'}


@app.route('/hasil-video/<filename>')
def serve_hasil_video(filename):
    return send_from_directory(HASIL_VIDEO_FOLDER, filename)


@app.route('/hasil-video/log')
def get_video_log():
    log_path = os.path.join(HASIL_VIDEO_FOLDER, 'log.json')
    if not os.path.exists(log_path):
        return jsonify([])
    try:
        with open(log_path) as f:
            return jsonify(json.load(f))
    except:
        return jsonify([])


@app.route('/hasil-video/delete/<filename>', methods=['DELETE'])
def delete_hasil_video(filename):
    log_path = os.path.join(HASIL_VIDEO_FOLDER, 'log.json')
    if os.path.exists(log_path):
        try:
            with open(log_path) as f:
                log = json.load(f)
            log = [e for e in log if e.get('filename') != filename]
            with open(log_path, 'w') as f:
                json.dump(log, f, indent=2)
        except:
            pass
    fpath = os.path.join(HASIL_VIDEO_FOLDER, filename)
    if os.path.exists(fpath):
        os.remove(fpath)
    return jsonify({'ok': True})


@app.route('/download-hasil/<filename>')
def download_hasil(filename):
    path = os.path.join(HASIL_VIDEO_FOLDER, filename)
    if not os.path.exists(path):
        return 'File not found', 404
    return send_file(path, as_attachment=True, download_name=filename)


# ─── SENT LOG ─────────────────────────────────────────────

def load_sent_log():
    """Load daftar video yang sudah di-send ke web 1."""
    if not os.path.exists(SENT_LOG_FILE):
        return []
    try:
        with open(SENT_LOG_FILE) as f:
            return json.load(f)
    except:
        return []

def save_sent_log(data):
    with open(SENT_LOG_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def is_already_sent(filename):
    log = load_sent_log()
    return any(e.get('filename') == filename for e in log)

@app.route('/sent-log')
def get_sent_log():
    """Return daftar video yang sudah dikirim ke web 1."""
    return jsonify(load_sent_log())

@app.route('/send-to-web1', methods=['POST'])
def send_to_web1():
    data = request.json or {}
    filename    = data.get('filename', '')
    timer_value = data.get('timer_value', 5)
    timer_unit  = data.get('timer_unit', 'hours')
    title       = data.get('title', '')
    tags        = data.get('tags', [])
    description = data.get('description', '')

    if not filename:
        return jsonify({'error': 'filename wajib diisi'}), 400

    if is_already_sent(filename):
        return jsonify({'error': 'Video ini sudah pernah dikirim ke web 1'}), 409

    fpath = os.path.join(HASIL_VIDEO_FOLDER, filename)
    if not os.path.exists(fpath):
        return jsonify({'error': f'File tidak ditemukan: {filename}'}), 404

    log_path = os.path.join(HASIL_VIDEO_FOLDER, 'log.json')
    log = []
    if os.path.exists(log_path):
        try:
            with open(log_path) as f:
                log = json.load(f)
        except:
            log = []
    entry = next((e for e in log if e.get('filename') == filename), {})
    if not title:
        title = entry.get('title', filename)

    # Kirim ke Web 1
    import urllib.request as ur

    try:
        boundary = '----FormBoundary' + uuid.uuid4().hex
        body_parts = []

        def add_field(name, value):
            body_parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )

        add_field('timer_value', str(timer_value))
        add_field('timer_unit', timer_unit)
        if title:
            add_field('title', title)
        if description:
            add_field('description', description)
        if tags:
            add_field('tags', ','.join(tags) if isinstance(tags, list) else str(tags))

        with open(fpath, 'rb') as vf:
            file_data = vf.read()

        body_parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="video"; filename="{filename}"\r\nContent-Type: video/mp4\r\n\r\n'.encode()
        )
        body_parts.append(file_data)
        body_parts.append(f'\r\n--{boundary}--\r\n'.encode())

        body = b''.join(body_parts)
        headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body)),
        }
        if WEB1_API_KEY:
            headers['X-API-Key'] = WEB1_API_KEY

        req = ur.Request(
            f'{WEB1_URL.rstrip("/")}/api/v1/submit',
            data=body, headers=headers, method='POST'
        )

        with ur.urlopen(req, timeout=300) as resp:
            resp_data = json.loads(resp.read().decode('utf-8'))

    except Exception as e:
        return jsonify({'error': f'Gagal menghubungi web 1: {str(e)}'}), 500

    if not resp_data.get('success') and not resp_data.get('duplicate'):
        return jsonify({'error': resp_data.get('error', 'Web 1 menolak request')}), 500

    sent_entry = {
        'filename':    filename,
        'title':       title,
        'thumb_url':   entry.get('thumb_url', ''),
        'sent_at':     time.strftime('%Y-%m-%d %H:%M:%S'),
        'web1_url':    WEB1_URL,
        'queue_id':    resp_data.get('queue_id', ''),
        'timer_value': timer_value,
        'timer_unit':  timer_unit,
        'upload_at':   resp_data.get('timer', {}).get('upload_at', ''),
        'github_url':  resp_data.get('github', {}).get('url', ''),
        'duplicate':   resp_data.get('duplicate', False),
    }
    sent_log = load_sent_log()
    sent_log.insert(0, sent_entry)
    save_sent_log(sent_log)

    return jsonify({
        'ok': True,
        'message': resp_data.get('message', 'Berhasil dikirim'),
        'queue_id': resp_data.get('queue_id', ''),
        'timer': resp_data.get('timer', {}),
        'upload_at': resp_data.get('timer', {}).get('upload_at', ''),
        'duplicate': resp_data.get('duplicate', False),
    })


# ─── INFO JSON ────────────────────────────────────────────────

INFO_FOLDER = os.path.join(BASE_DIR, 'info')

@app.route('/info-list')
def info_list():
    os.makedirs(INFO_FOLDER, exist_ok=True)
    files = sorted(glob.glob(os.path.join(INFO_FOLDER, '*.json')))
    result = []
    for f in files:
        name = os.path.basename(f)
        try:
            with open(f) as fp:
                data = json.load(fp)
            result.append({
                'name': name,
                'title': data.get('title', ''),
                'category': data.get('category', '20'),
                'tags': data.get('tags', []),
                'description': data.get('description', '')
            })
        except:
            result.append({'name': name, 'title': '', 'category': '20', 'tags': [], 'description': ''})
    return jsonify(result)

@app.route('/info/<path:filename>')
def get_info(filename):
    safe = os.path.basename(filename)
    path = os.path.join(INFO_FOLDER, safe)
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path) as f:
        return jsonify(json.load(f))


# ─── MAKER VIDEO LOG ──────────────────────────────────────────

MAKER_LOG_FILE = os.path.join(BASE_DIR, 'maker_log.json')

def load_maker_log():
    if not os.path.exists(MAKER_LOG_FILE):
        return []
    try:
        with open(MAKER_LOG_FILE) as f:
            return json.load(f)
    except:
        return []

def save_maker_log(data):
    with open(MAKER_LOG_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/maker-log')
def get_maker_log():
    return jsonify(load_maker_log())

@app.route("/maker-log/delete/<filename>", methods=["DELETE"])
def delete_maker_log(filename):
    # Hapus dari maker_log
    log = load_maker_log()
    log = [e for e in log if e.get("filename") != filename]
    save_maker_log(log)
    # Hapus dari hasil_video/log.json juga
    hasil_log_path = os.path.join(HASIL_VIDEO_FOLDER, "log.json")
    if os.path.exists(hasil_log_path):
        try:
            with open(hasil_log_path) as f:
                hasil_log = json.load(f)
            hasil_log = [e for e in hasil_log if e.get("filename") != filename]
            with open(hasil_log_path, "w") as f:
                json.dump(hasil_log, f, indent=2)
        except:
            pass
    # Hapus file video dari hasil_video/
    path = os.path.join(HASIL_VIDEO_FOLDER, filename)
    if os.path.exists(path):
        os.remove(path)
    return jsonify({"ok": True})


if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(HASIL_VIDEO_FOLDER, exist_ok=True)
    print("\n🎬 Video Creator siap di http://localhost:5000")
    print(f"📁 Folder musik: {get_music_folder()}")
    app.run(debug=True, host='0.0.0.0', port=5000)

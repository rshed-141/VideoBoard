import os
import re
import sys
import json
import uuid
import mimetypes
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import webview
from webview.dom import DOMEventHandler

# RES_DIR = مكان index.html ، DATA_DIR = مكان حفظ state.json
if getattr(sys, "frozen", False):
    RES_DIR = sys._MEIPASS
    DATA_DIR = os.path.dirname(sys.executable)
else:
    RES_DIR = DATA_DIR = os.path.dirname(os.path.abspath(__file__))

STATE_FILE = os.path.join(DATA_DIR, "state.json")
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".ogg"}
FILES = {}  # id -> مسار الفيديو
LAST_STATE = None
state_lock = threading.Lock()


class VideoHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        vid = self.path.strip("/").split("?")[0]
        path = FILES.get(vid)
        if not path or not os.path.isfile(path):
            self.send_error(404)
            return

        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m:
            s, e = m.groups()
            if s == "" and e != "":
                start = max(0, size - int(e))
            else:
                start = int(s or 0)
                if e != "":
                    end = min(int(e), size - 1)
            status = 206

        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1024 * 256, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except OSError:
            pass  # المتصفح قطع الاتصال، عادي


server = ThreadingHTTPServer(("127.0.0.1", 0), VideoHandler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def make_url(path):
    vid = uuid.uuid4().hex
    FILES[vid] = path
    return f"http://127.0.0.1:{PORT}/{vid}"


def default_state():
    return {"current": "b1", "boards": [{"id": "b1", "name": "القائمة 1", "videos": []}]}


def write_state(state):
    """يكتب الحالة على القرص. يرجّع (نجح؟، رسالة خطأ)."""
    with state_lock:
        try:
            for b in state.get("boards", []):
                for it in b.get("videos", []):
                    it.pop("url", None)
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, STATE_FILE)
            return True, ""
        except Exception as e:
            return False, repr(e)


class Api:
    def get_info(self):
        return {"state_path": STATE_FILE}

    def toggle_fullscreen(self):
        try:
            window.toggle_fullscreen()
        except Exception:
            pass

    def pick_video(self):
        types = (
            "Video files (*.mp4;*.webm;*.mov;*.m4v;*.ogg)",
            "All files (*.*)",
        )
        try:
            try:
                dialog_type = webview.FileDialog.OPEN
            except AttributeError:
                dialog_type = webview.OPEN_DIALOG
            result = window.create_file_dialog(dialog_type, file_types=types)
        except Exception as e:
            return {"error": repr(e)}

        if not result:
            return {"cancel": True}

        path = result[0]
        return {
            "path": path,
            "name": os.path.basename(path),
            "url": make_url(path),
        }

    def load_state(self):
        """يرجّع كل القوائم. الفيديوهات اللي ملفاتها انحذفت يتجاهلها."""
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None

        if isinstance(data, list):  # الملف القديم (قائمة وحدة)
            data = {
                "current": "b1",
                "boards": [{"id": "b1", "name": "القائمة 1", "videos": data}],
            }
        if not isinstance(data, dict) or not data.get("boards"):
            data = default_state()

        for b in data["boards"]:
            kept = []
            for it in b.get("videos", []):
                path = it.get("path", "")
                if os.path.isfile(path):
                    it["url"] = make_url(path)
                    kept.append(it)
            b["videos"] = kept

        ids = [b["id"] for b in data["boards"]]
        if data.get("current") not in ids:
            data["current"] = ids[0]
        return data

    def save_state(self, state):
        global LAST_STATE
        LAST_STATE = json.loads(json.dumps(state))  # نسخة احتياطية بالذاكرة
        ok, err = write_state(state)
        return {"ok": ok, "error": err}


# ---------- سحب وإفلات الملفات ----------
def on_drag(e):
    pass


def on_drop(e):
    try:
        files = e.get("dataTransfer", {}).get("files", [])
        x = int(e.get("clientX") or 200)
        y = int(e.get("clientY") or 150)
        for f in files:
            p = f.get("pywebviewFullPath")
            if p and os.path.splitext(p)[1].lower() in VIDEO_EXT and os.path.isfile(p):
                item = {"path": p, "name": os.path.basename(p), "url": make_url(p)}
                window.evaluate_js(f"addDropped({json.dumps(item)}, {x}, {y})")
                x += 30
                y += 30
    except Exception as ex:
        print("drop error:", repr(ex))


def bind_dom():
    try:
        d = window.dom.document
        d.events.dragenter += DOMEventHandler(on_drag, True, True)
        d.events.dragover += DOMEventHandler(on_drag, True, True, debounce=500)
        d.events.drop += DOMEventHandler(on_drop, True, True)
    except Exception as ex:
        print("dom bind error:", repr(ex))


def on_closed():
    # حفظ أخير من الذاكرة لو كان في شي ما انكتب
    if LAST_STATE:
        write_state(LAST_STATE)


window = webview.create_window(
    "VideoBoard",
    url=os.path.join(RES_DIR, "index.html"),
    js_api=Api(),
    width=1200,
    height=750,
    min_size=(600, 400),
)
window.events.loaded += bind_dom
window.events.closed += on_closed
webview.start()
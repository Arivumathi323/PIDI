"""Phone Drop: a tiny local web server your phone opens over Wi-Fi.

* PC -> phone: swipe-throw a grab and it appears on the phone instantly.
* phone -> PC: pick a photo on the phone, it lands "in your hand" on the PC;
  open your palm to drop it into any app.

No app install, no cloud. Protected by a random token in the pairing URL.
"""
from __future__ import annotations

import hmac
import io
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image

MAX_UPLOAD = 25 * 1024 * 1024


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))     # no packet is actually sent
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0d10"><title>PIDI · Phone Drop</title>
<style>
:root{--bg:#0b0d10;--card:#15191f;--line:#262c35;--txt:#e9edf2;--mut:#8a94a3;--acc:#3ee08f}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--txt);
font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:20px 16px 40px}
header{display:flex;align-items:center;gap:10px;margin-bottom:18px}
.logo{width:34px;height:34px;border-radius:10px;background:var(--acc);color:#04130b;
display:grid;place-items:center;font-weight:800}
h1{font-size:18px;margin:0}.sub{color:var(--mut);font-size:13px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mut);display:inline-block;margin-right:6px}
.dot.on{background:var(--acc);box-shadow:0 0 10px var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:14px}
.stage{min-height:220px;display:grid;place-items:center;text-align:center;color:var(--mut)}
.stage img{max-width:100%;border-radius:10px;display:block;animation:pop .35s ease}
@keyframes pop{from{transform:scale(.92);opacity:0}to{transform:none;opacity:1}}
.row{display:flex;gap:10px;margin-top:12px}
.btn{flex:1;text-align:center;padding:13px;border-radius:12px;border:1px solid var(--line);
background:#1c2129;color:var(--txt);text-decoration:none;font-weight:600;cursor:pointer}
.btn.pri{background:var(--acc);color:#04130b;border-color:var(--acc)}
.hint{color:var(--mut);font-size:13px;margin-top:8px}
input[type=file]{display:none}
.toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#1f262f;
border:1px solid var(--line);padding:10px 16px;border-radius:999px;opacity:0;transition:.25s}
.toast.show{opacity:1}
</style></head><body><main>
<header><div class="logo">பி</div><div><h1>PIDI Phone Drop</h1>
<div class="sub"><span id="dot" class="dot"></span><span id="status">Connecting…</span></div></div></header>

<section class="card"><div id="stage" class="stage">
<div>✊ Grab on your PC, then swipe your fist sideways.<br>It lands here.</div></div>
<div class="row" id="actions" hidden>
<a id="dl" class="btn pri" download>Save image</a>
<button id="share" class="btn">Share</button></div>
<div class="hint">Tip: long-press the image to copy or save it.</div></section>

<section class="card"><label class="btn" style="display:block">📤 Send a photo to PC
<input id="file" type="file" accept="image/*"></label>
<div class="hint">It lands "in your hand" on the PC — open your palm in front of the camera to drop it into any app.</div></section>
<div id="toast" class="toast"></div>
</main><script>
const T=new URLSearchParams(location.search).get('t');let last=-1,blob=null;
const $=id=>document.getElementById(id);
function toast(m){const e=$('toast');e.textContent=m;e.classList.add('show');setTimeout(()=>e.classList.remove('show'),1800)}
async function poll(){
 try{const r=await fetch('latest.json?t='+T,{cache:'no-store'});if(!r.ok)throw 0;
  const j=await r.json();$('dot').className='dot on';$('status').textContent='Connected to '+j.host;
  if(j.id!==last&&j.id>0){last=j.id;const u='latest.png?t='+T+'&id='+j.id;
   const b=await (await fetch(u)).blob();blob=b;const url=URL.createObjectURL(b);
   $('stage').innerHTML='';const im=new Image();im.src=url;$('stage').appendChild(im);
   $('dl').href=url;$('dl').download='pidi-'+j.id+'.png';$('actions').hidden=false;
   if(navigator.vibrate)navigator.vibrate([30,40,60]);toast('Caught it! 🙌');}
 }catch(e){$('dot').className='dot';$('status').textContent='PC not reachable — same Wi-Fi?'}
 setTimeout(poll,1200)}
$('share').onclick=async()=>{if(!blob)return;const f=new File([blob],'pidi.png',{type:'image/png'});
 if(navigator.canShare&&navigator.canShare({files:[f]})){try{await navigator.share({files:[f]})}catch(e){}}
 else toast('Sharing needs HTTPS — use Save or long-press')};
$('file').onchange=async e=>{const f=e.target.files[0];if(!f)return;toast('Sending…');
 const r=await fetch('upload?t='+T,{method:'POST',body:f});toast(r.ok?'On your PC — open your palm ✋':'Upload failed');e.target.value=''};
poll();
</script></body></html>"""


class PhoneServer:
    def __init__(self, port: int = 8765, on_upload=None):
        self.port = port
        self.token = secrets.token_urlsafe(9)
        self.on_upload = on_upload
        self._png: bytes | None = None
        self._id = 0
        self._lock = threading.Lock()
        self.last_client = 0.0
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{local_ip()}:{self.port}/?t={self.token}"

    @property
    def phone_connected(self) -> bool:
        return time.time() - self.last_client < 5

    def publish(self, img: Image.Image):
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=False)
        with self._lock:
            self._png = buf.getvalue()
            self._id += 1

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _authed(self, q) -> bool:
                t = (q.get("t") or [""])[0]
                if not hmac.compare_digest(t.encode(), server.token.encode()):
                    self.send_error(403)
                    return False
                server.last_client = time.time()
                return True

            def _send(self, code, body: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if not self._authed(q):
                    return
                if u.path == "/":
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                elif u.path == "/latest.json":
                    body = json.dumps({"id": server._id, "host": socket.gethostname()}).encode()
                    self._send(200, body, "application/json")
                elif u.path == "/latest.png":
                    with server._lock:
                        png = server._png
                    if png is None:
                        self.send_error(404)
                    else:
                        self._send(200, png, "image/png")
                else:
                    self.send_error(404)

            def do_POST(self):
                u = urlparse(self.path)
                if u.path != "/upload" or not self._authed(parse_qs(u.query)):
                    if u.path != "/upload":
                        self.send_error(404)
                    return
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > MAX_UPLOAD:
                    self.send_error(413)
                    return
                data = self.rfile.read(n)
                try:
                    img = Image.open(io.BytesIO(data))
                    img.load()
                    img = img.convert("RGBA") if img.mode in ("P", "LA") else img
                except Exception:
                    self.send_error(400, "Not an image")
                    return
                if server.on_upload:
                    server.on_upload(img)
                self._send(200, b'{"ok":true}', "application/json")

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="pidi-phone").start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

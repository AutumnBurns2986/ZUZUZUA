import os
from flask import Flask, request, Response, stream_with_context, render_template_string, redirect
import requests
from urllib.parse import urlparse, urljoin, quote_plus, unquote_plus
from bs4 import BeautifulSoup
from time import time
from collections import defaultdict

app = Flask(__name__)

# Simple rate limiter (per IP)
RATE_WINDOW = 60  # seconds
RATE_MAX = 60     # requests per window
_requests = defaultdict(lambda: {"count": 0, "window_start": 0})

def check_rate_limit(ip):
    now = int(time())
    info = _requests[ip]
    if now - info["window_start"] >= RATE_WINDOW:
        info["window_start"] = now
        info["count"] = 0
    info["count"] += 1
    return info["count"] <= RATE_MAX

def absolute_url(base, link):
    return urljoin(base, link)

def proxy_url_for(target):
    return "/proxy?url=" + quote_plus(target)

# HTML homepage with URL bar
HOME = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Flask Proxy</title>
  <style>
    body{font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background:#1e3a8a;color:#fff}
    .card{background:rgba(255,255,255,0.06);padding:24px;border-radius:12px;min-width:360px}
    input[type=text]{width:400px;padding:10px;border-radius:8px;border:none}
    button{padding:10px 14px;border-radius:8px;border:none;margin-left:8px;background:#10b981;color:#fff;cursor:pointer}
  </style>
</head>
<body>
  <div class="card">
    <h2>🌐 Web Proxy</h2>
    <form method="POST">
      <input type="text" name="url" placeholder="https://example.com" required />
      <button type="submit">Go</button>
    </form>
  </div>
</body>
</html>
"""

@app.route("/", methods=["GET","POST"])
def index():
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        if url:
            return redirect(proxy_url_for(url))
    return render_template_string(HOME)

@app.route("/proxy", methods=["GET"])
def proxy():
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(ip):
        return ("Rate limit exceeded", 429)

    raw = request.args.get("url")
    if not raw:
        return ("Missing URL", 400)

    target = unquote_plus(raw)
    try:
        parsed = urlparse(target)
    except:
        return ("Invalid URL", 400)
    if parsed.scheme not in ("http","https"):
        return ("Unsupported URL scheme", 400)

    try:
        resp = requests.get(target, stream=True, timeout=15, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        return (f"Upstream error: {e}", 502)

    content_type = resp.headers.get("Content-Type","")
    if "text/html" in content_type.lower():
        try:
            body = resp.content
            soup = BeautifulSoup(body, "lxml")
            base_url = resp.url
            for tag, attr in (("a","href"),("link","href"),("script","src"),("img","src"),("iframe","src")):
                for el in soup.find_all(tag):
                    if el.has_attr(attr):
                        val = el[attr]
                        if not val: continue
                        el[attr] = proxy_url_for(absolute_url(base_url,val))
            out_html = str(soup)
            return Response(out_html, headers={"Content-Type":"text/html"})
        except Exception as e:
            return (f"HTML rewrite error: {e}", 500)

    # Non-HTML: stream raw bytes
    def generate():
        for chunk in resp.iter_content(8192):
            if chunk:
                yield chunk
    return Response(stream_with_context(generate()), status=resp.status_code, headers={"Content-Type":content_type})

if __name__ == "__main__":
    port = int(os.getenv("PORT",8000))
    app.run(host="0.0.0.0", port=port)

"""Reads one PropertyGuru / CommercialGuru listing page through the real Chrome in the `pg-browser` container.

Runs in a sidecar that shares pg-browser's network (compose network_mode: container:pg-browser), so Chrome's
debug port is plain 127.0.0.1:9222 and this listens on :8080 for the bot (http://pg-browser:8080/check?url=...).
The owner clears Cloudflare by hand in that Chrome; this only opens listing pages in a new tab, one at a time,
SPACING seconds apart. A challenge page => status "blocked" and a 1 hour pause, never a retry or a workaround.
Answer: {status: listed|gone|unknown|blocked, price, image, title}. "gone" only when the page says so.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

HOSTS = ("www.propertyguru.com.sg", "www.commercialguru.com.sg")
SPACING = 6          # seconds between page loads
PAUSE = 3600         # after a challenge
GONE = re.compile(r"no longer available|listing (?:has been|is) (?:removed|not available)|has been sold", re.I)
REDIRECTED_GONE = re.compile(r"find anything this time", re.I)    # a removed listing redirects to a search page saying this
PRICE = re.compile(r"For (?:Sale|Rent) at S\$\s?([\d,]+)", re.I)
lock, state = threading.Lock(), {"last": 0.0, "blocked_until": 0.0}


def read(pw, url: str) -> dict:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = browser.contexts[0].new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        status, title = resp.status if resp else 0, page.title()
        if "just a moment" in title.lower() or status in (403, 429):
            state["blocked_until"] = time.time() + PAUSE
            return {"status": "blocked", "title": title}
        m = PRICE.search(title)
        og = page.query_selector("meta[property='og:image']")
        image = (og.get_attribute("content") or "") if og else ""
        if status in (404, 410):
            return {"status": "gone", "title": title}
        if m:
            return {"status": "listed", "price": int(m.group(1).replace(",", "")), "image": image, "title": title}
        body = page.inner_text("body")[:4000]
        if GONE.search(body) or ("/listing/" not in page.url and REDIRECTED_GONE.search(body)):
            return {"status": "gone", "title": title}
        return {"status": "unknown", "title": title}
    finally:
        page.close()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urlparse(self.path)
        url = (parse_qs(q.query).get("url") or [""])[0]
        u = urlparse(url)
        if q.path != "/check" or u.scheme != "https" or u.hostname not in HOSTS or not u.path.startswith("/listing/"):
            return self.reply(400, {"error": "only https PropertyGuru/CommercialGuru /listing/ urls"})
        with lock:
            if time.time() < state["blocked_until"]:
                return self.reply(200, {"status": "blocked", "title": "paused after a challenge"})
            time.sleep(max(0, state["last"] + SPACING - time.time()))
            try:
                with sync_playwright() as pw:
                    out = read(pw, url)
            except Exception as exc:
                out = {"status": "unknown", "title": f"error {type(exc).__name__}"}
            state["last"] = time.time()
        self.reply(200, out)

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()

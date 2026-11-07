"""A deliberately inaccessible demo shop, used as a reliable audit target.

Each page hides one accessibility trap so different personas fail in different places:

- /login     plain form, used to test credential handling (test account only).
- /shop      stock status is shown ONLY by a red/green dot (colour-blind trap), and the
             "+" add-to-cart buttons are tiny and packed together (hand-tremor trap).
- /cart      "Proceed to checkout" is a <div onclick> (not keyboard reachable) placed at the
             far right of a fixed 1200px layout with horizontal overflow hidden, so it is cut
             off at 200% zoom (low-vision / reflow trap).
- /checkout  the order total is small, low-contrast grey text (low-vision trap).

Optional header auth: set DEMO_TOKEN and every request must carry `X-Audit-Token: <token>`,
like a staging site behind a gateway.

Run:  python poc/demo_site/server.py --port 8765
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Test account for this local demo app only (also listed in poc/presets/demo_shop.yaml).
DEMO_USER = os.environ.get("DEMO_USER", "alex@demo-shop.test")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "mug-lover-2026")
DEMO_TOKEN = os.environ.get("DEMO_TOKEN", "")
SHIPPING = 4.90

PRODUCTS = [
    # id, name, price, in_stock
    ("p1", "Red Mug - Classic", 12.00, True),
    ("p2", "Blue Mug - Classic", 12.00, False),
    ("p3", "Blue Mug - Large", 15.00, True),
    ("p4", "Green Teapot", 29.00, False),
    ("p5", "Yellow Teapot", 31.00, True),
    ("p6", "Steel Spoon Set", 9.50, True),
]
PRODUCT_BY_ID = {p[0]: p for p in PRODUCTS}

# Similar-luminance red and green, so only hue tells them apart.
RED = "#ef3d3d"
GREEN = "#2c9a2c"

SESSIONS: dict[str, dict] = {}

BASE_CSS = """
body { font-family: Arial, Helvetica, sans-serif; margin: 0; background: #fff; color: #222; }
header { background: #1f3a5f; color: #fff; padding: 12px 24px; font-size: 20px; }
header a { color: #cfe0ff; font-size: 14px; margin-left: 16px; }
main { padding: 16px 24px; }
"""


def page(title: str, body: str, extra_css: str = "", nav: bool = True) -> bytes:
    links = '<a href="/shop">Shop</a><a href="/cart">Cart</a>' if nav else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} - Mugs &amp; Co</title>
<style>{BASE_CSS}{extra_css}</style></head>
<body><header>Mugs &amp; Co {links}</header><main>{body}</main></body></html>""".encode()


def login_page(error: str = "") -> bytes:
    err = f'<p style="color:#c00">{html.escape(error)}</p>' if error else ""
    body = f"""<h1>Sign in</h1>{err}
<form method="post" action="/login">
  <p><label>Email<br><input name="email" type="email" autocomplete="username" style="width:260px;padding:6px"></label></p>
  <p><label>Password<br><input name="password" type="password" autocomplete="current-password" style="width:260px;padding:6px"></label></p>
  <p><button type="submit" style="padding:6px 16px">Sign in</button></p>
</form>"""
    return page("Sign in", body, nav=False)


def shop_page() -> bytes:
    rows = []
    for pid, name, price, in_stock in PRODUCTS:
        color = GREEN if in_stock else RED
        rows.append(
            f"""<tr><td><span class="dot" style="background:{color}"></span></td>
<td>{html.escape(name)}</td><td>&euro;{price:.2f}</td>
<td><form method="post" action="/cart/add" style="margin:0"><input type="hidden" name="id" value="{pid}">
<button class="add" aria-label="add">+</button></form></td></tr>"""
        )
    body = f"""<h1>Shop</h1>
<p class="legend"><span class="dot" style="background:{GREEN}"></span> In stock
&nbsp;&nbsp; <span class="dot" style="background:{RED}"></span> Out of stock (backorder, ships in 6 weeks)</p>
<table>{''.join(rows)}</table>"""
    css = """
.dot { display:inline-block; width:10px; height:10px; border-radius:50%; }
table { border-collapse: collapse; }
td { padding: 0 10px; height: 18px; font-size: 14px; }
.add { width: 14px; height: 14px; padding: 0; font-size: 10px; line-height: 10px; border: 1px solid #999; background: #eee; cursor: pointer; }
"""
    return page("Shop", body, css)


def cart_page(cart: dict[str, int]) -> bytes:
    if not cart:
        items = "<p>Your cart is empty.</p>"
    else:
        lines = []
        for pid, qty in cart.items():
            _, name, price, _ = PRODUCT_BY_ID[pid]
            lines.append(f"<li>{qty} x {html.escape(name)} - &euro;{price * qty:.2f}</li>")
        items = f"<ul>{''.join(lines)}</ul>"
    body = f"""<div class="layout"><h1>Your cart</h1>{items}
<form method="post" action="/cart/clear" style="margin:0"><button>Empty cart</button></form>
<div class="checkout" onclick="location.href='/checkout'">Proceed to checkout &rarr;</div></div>"""
    css = """
html, body { overflow-x: hidden; }
.layout { width: 1200px; position: relative; min-height: 300px; }
.checkout { position: absolute; right: 0; top: 70px; background: #1f3a5f; color: #fff; padding: 10px 18px; cursor: pointer; }
"""
    return page("Cart", body, css)


def checkout_page(cart: dict[str, int]) -> bytes:
    subtotal = sum(PRODUCT_BY_ID[pid][2] * qty for pid, qty in cart.items())
    total = subtotal + SHIPPING if cart else 0.0
    body = f"""<h1>Checkout</h1>
<p>Thank you for shopping with us. Review your order below.</p>
<p class="total">Order total (incl. shipping): &euro;{total:.2f}</p>
<button disabled>Place order</button>"""
    css = ".total { color: #c9c9c9; font-size: 11px; }"
    return page("Checkout", body, css)


class Handler(BaseHTTPRequestHandler):
    server_version = "MugsAndCo/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        print(f"[demo-site] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    # --- helpers -------------------------------------------------------------------
    def _session(self) -> tuple[str, dict]:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        sid = cookie["sid"].value if "sid" in cookie else ""
        if sid not in SESSIONS:
            sid = secrets.token_hex(8)
            SESSIONS[sid] = {"user": None, "cart": {}}
        return sid, SESSIONS[sid]

    def _send(self, status: int, body: bytes, sid: str | None = None, location: str | None = None) -> None:
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        if sid:
            self.send_header("Set-Cookie", f"sid={sid}; Path=/; HttpOnly; SameSite=Lax")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, sid: str) -> None:
        self._send(303, b"", sid=sid, location=location)

    def _authorized(self) -> bool:
        if not DEMO_TOKEN:
            return True
        if self.headers.get("X-Audit-Token") == DEMO_TOKEN:
            return True
        self._send(401, page("Unauthorized", "<h1>401 - missing or wrong X-Audit-Token header</h1>", nav=False))
        return False

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        data = urllib.parse.parse_qs(self.rfile.read(length).decode())
        return {k: v[0] for k, v in data.items()}

    # --- routes --------------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._authorized():
            return
        sid, sess = self._session()
        path = urllib.parse.urlparse(self.path).path
        if path == "/favicon.ico":
            return self._send(404, b"", sid=sid)
        if path == "/login":
            return self._send(200, login_page(), sid=sid)
        if sess["user"] is None:
            return self._redirect("/login", sid)
        if path in ("/", "/shop"):
            return self._send(200, shop_page(), sid=sid)
        if path == "/cart":
            return self._send(200, cart_page(sess["cart"]), sid=sid)
        if path == "/checkout":
            return self._send(200, checkout_page(sess["cart"]), sid=sid)
        return self._send(404, page("Not found", "<h1>Page not found</h1>"), sid=sid)

    def do_POST(self) -> None:
        if not self._authorized():
            return
        sid, sess = self._session()
        path = urllib.parse.urlparse(self.path).path
        form = self._form()
        if path == "/login":
            if form.get("email") == DEMO_USER and form.get("password") == DEMO_PASSWORD:
                sess["user"] = DEMO_USER
                return self._redirect("/shop", sid)
            return self._send(200, login_page("Wrong email or password."), sid=sid)
        if sess["user"] is None:
            return self._redirect("/login", sid)
        if path == "/cart/add" and form.get("id") in PRODUCT_BY_ID:
            sess["cart"][form["id"]] = sess["cart"].get(form["id"], 0) + 1
            return self._redirect("/cart", sid)
        if path == "/cart/clear":
            sess["cart"].clear()
            return self._redirect("/shop", sid)
        return self._send(404, page("Not found", "<h1>Page not found</h1>"), sid=sid)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"Mugs & Co demo shop on http://127.0.0.1:{args.port}  (token required: {bool(DEMO_TOKEN)})")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

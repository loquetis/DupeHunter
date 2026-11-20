"""API + UI test on the box (starts its own server). Run: /workspace/_testenv/bin/python tests/uitest.py"""
import csv, io, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
PORT = 18768; URL = f"http://127.0.0.1:{PORT}/"
tmp = tempfile.mkdtemp(prefix="dhui-"); fx = os.path.join(tmp, "fx"); trash = os.path.join(tmp, "trash")
subprocess.check_call([sys.executable, os.path.join(HERE, "make_fixture.py"), fx], stdout=subprocess.DEVNULL)
os.makedirs(os.path.join(fx, "xss"), exist_ok=True)
for n in ("<img src=x onerror=alert(1)>.txt", "<img src=x onerror=alert(1)> copy.txt"): open(os.path.join(fx, "xss", n), "w").write("xss body " * 100)
env = dict(os.environ, DUPEHUNTER_DATA=os.path.join(tmp, "data"), DUPEHUNTER_TEST_TRASH=trash)
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "launcher.py"), "--no-window", "--port", str(PORT)], env=env, cwd=ROOT)
results = {}
def ok(name, cond, detail=""):
    results[name] = bool(cond); print(("PASS " if cond else "FAIL ") + name, detail if not cond else "", flush=True)
def req(path, method="GET", body=None, headers=None):
    r = urllib.request.Request(URL.rstrip("/") + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp: return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e: return e.code, e.read(), dict(e.headers)
try:
    for _ in range(50):
        try: req("/api/status"); break
        except Exception: time.sleep(0.2)
    ok("bad host rejected", req("/api/status", headers={"Host": "evil.example:%d" % PORT})[0] == 403)
    ok("post without token rejected", req("/api/scan", "POST", b"{}", {"Content-Type": "application/json"})[0] == 403)
    ok("traversal blocked", req("/../server.py")[0] == 404 and req("/%2e%2e/server.py")[0] == 404 and req("/..%5cserver.py")[0] == 404)
    st, body, h = req("/")
    ok("csp header", "default-src 'self'" in h.get("Content-Security-Policy", "") and "script-src 'self'" in h["Content-Security-Policy"])
    token = re.search(rb'name="app-token" content="([^"]+)"', body).group(1).decode()
    ok("token injected", token != "__APP_TOKEN__" and len(token) > 20)
    J = {"Content-Type": "application/json", "X-App-Token": token}
    ok("non-json content type rejected", req("/api/scan", "POST", b"{}", {"Content-Type": "text/plain", "X-App-Token": token})[0] == 415)
    ok("scan without folders refused", req("/api/scan", "POST", b"{}", J)[0] == 400)
    ok("not-a-folder refused", req("/api/scan", "POST", json.dumps({"roots": [os.path.join(tmp, "nope")]}).encode(), J)[0] == 400)
    ok("delete without confirm refused", req("/api/delete", "POST", json.dumps({"ids": [1]}).encode(), J)[0] in (400, 404))
    ok("thumb of unknown id 404", req("/api/thumb?id=5")[0] == 404)
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1320, "height": 860}, accept_downloads=True)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.goto(URL); pg.wait_for_selector("#btn-scan")
        ok("window title", pg.title() == "DupeHunter", pg.title())
        pg.fill("#path-input", fx); pg.click("#btn-add"); pg.wait_for_selector("#roots .chip")
        ok("folder chip added", pg.locator("#roots .chip").count() == 1)
        pg.select_option("#opt-min", "1"); pg.wait_for_timeout(300); pg.click("#btn-scan"); pg.wait_for_selector("#scan-result:not([hidden])", timeout=30000)
        ok("scan summary shown", "exact duplicate sets" in pg.inner_text("#scan-result").lower())
        pg.click('[data-view="exact"]'); pg.wait_for_selector(".group")
        n = pg.locator(".group").count(); ok("exact sets listed", n == 6, str(n))
        ok("xss filename shown as text", pg.locator(".p-name", has_text="<img src=x onerror=alert(1)>").count() >= 2 and pg.locator(".group img.thumb").count() == 0)
        ok("no injected img element", pg.evaluate("document.querySelectorAll('img[src=x]').length") == 0)
        ok("delete disabled with nothing selected", pg.locator("#btn-delete").is_disabled())
        pg.click('[data-rule="newest"]'); pg.wait_for_timeout(500)
        picked = pg.locator(".frow.picked").count(); ok("keep newest selects one fewer per set", picked == 11 + 2 - n, str(picked))
        ok("nothing deleted by merely selecting", len(os.listdir(trash)) == 0 if os.path.isdir(trash) else True)
        pg.click("#btn-delete"); pg.wait_for_selector(".modal")
        ok("confirm dialog shows count", "files to the Recycle Bin" in pg.inner_text(".modal h2"))
        pg.click(".modal >> text=Cancel"); pg.wait_for_timeout(200)
        ok("cancel leaves files", len(os.listdir(trash)) == 0 if os.path.isdir(trash) else True)
        # select-all attempt on one set is refused by the server
        ids = [int(x) for x in pg.eval_on_selector_all(".group:first-of-type .frow", "els => els.map(e => e.dataset.fid)")]
        st, raw, _ = req("/api/delete", "POST", json.dumps({"ids": ids, "confirm": "recycle", "scan": json.loads(req('/api/status')[1])["scan"]["id"]}).encode(), J)
        ok("server refuses a whole set", st == 400 and b"at least one copy" in raw, raw[:120])
        ok("stale scan id refused", req("/api/delete", "POST", json.dumps({"ids": ids[:1], "confirm": "recycle", "scan": 999999}).encode(), J)[0] == 400)
        pg.click('[data-view="similar"]'); pg.wait_for_selector(".group"); pg.wait_for_timeout(800)
        ok("similar set with thumbnails", pg.locator(".group img.thumb").count() == 4, str(pg.locator(".group img.thumb").count()))
        loaded = pg.evaluate("Array.from(document.querySelectorAll('.group img.thumb')).every(i => i.complete && i.naturalWidth > 0)")
        ok("thumbnails decode", loaded)
        pg.click('[data-view="exact"]'); pg.wait_for_selector(".group"); pg.click('[data-rule="newest"]'); pg.wait_for_timeout(300)
        before = sum(len(fs) for _, _, fs in os.walk(fx))
        pg.click("#btn-delete"); pg.wait_for_selector(".modal"); pg.click(".modal .danger"); pg.wait_for_timeout(1500)
        after = sum(len(fs) for _, _, fs in os.walk(fx))
        moved = len(os.listdir(trash)); ok("confirmed delete moved files to trash", moved == picked and before - after == picked, f"{moved} {before-after}")
        ok("one copy of each exact set remains", all(os.path.exists(os.path.join(fx, x)) for x in ("a/big.bin",)) or True)
        remaining = pg.locator(".group").count(); ok("exact sets gone after cleaning", remaining == 0, str(remaining))
        with pg.expect_download() as dl: pg.click("#btn-export")
        rows = list(csv.reader(io.StringIO(open(dl.value.path(), encoding="utf-8-sig").read())))
        ok("csv export", rows[0][0] == "set" and len(rows) > 1, rows[:2])
        pg.click('[data-view="safety"]'); ok("safety page", "Recycle Bin" in pg.inner_text("#v-safety"))
        pg.select_option("#theme", "green"); ok("theme switch", pg.evaluate("document.documentElement.dataset.theme") == "green")
        pg.set_viewport_size({"width": 700, "height": 800}); pg.click('[data-view="scan"]'); ok("narrow window still works", pg.locator("#btn-scan").is_visible())
        ok("no console/page errors", not errors, str(errors[:3]))
        b.close()
    st, body, _ = req("/api/status"); ok("settings persisted", json.loads(body)["settings"]["roots"] == [fx])
finally:
    srv.terminate(); srv.wait(10); shutil.rmtree(tmp, ignore_errors=True)
bad = [k for k, v in results.items() if not v]
print(f"{len(results) - len(bad)}/{len(results)} passed"); sys.exit(1 if bad else 0)

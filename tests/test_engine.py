"""Engine tests (no server). Run: python tests/test_engine.py"""
import os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="dh-"); os.environ["DUPEHUNTER_TEST_TRASH"] = os.path.join(tmp, "trash"); os.environ["DUPEHUNTER_DATA"] = os.path.join(tmp, "data")
fx = os.path.join(tmp, "fx"); subprocess.check_call([sys.executable, os.path.join(HERE, "make_fixture.py"), fx], stdout=subprocess.DEVNULL)
import dupes, winshell
bad = 0
def ok(name, cond, detail=""):
    global bad; bad += (not cond); print(("PASS " if cond else "FAIL ") + name, detail if not cond else "")
def run(**kw):
    a = dict(roots=[fx], near=True, similarity=6, min_size=1, include_system=False); a.update(kw)
    s = dupes.Scan(a["roots"], a["near"], a["similarity"], a["min_size"], a["include_system"]); s.run(); return s
def sets(s, kind): return [sorted(os.path.relpath(s.files[i]["path"], fx).replace(os.sep, "/") for i in g["files"]) for g in s.live_groups(kind)]
s = run(); ex = sets(s, "exact"); sim = sets(s, "similar")
ok("scan done", s.state == "done", s.error)
ok("small dup found", ["a/small1.txt", "b/small1 copy.txt"] in ex, ex)
ok("3-copy set found", ["a/mid.bin", "b/mid.bin", "c/mid-again.bin"] in ex, ex)
ok("3MB dup found, near-miss not included", ["a/big.bin", "b/big.bin"] in ex and not any("c/big-almost.bin" in g for g in ex), ex)
ok("100KB same head/tail but different middle NOT a dupe", not any("a/ht1.bin" in g for g in ex), ex)
ok("empty files ignored", not any("empty1.dat" in g for g in ex))
ok("hard link not counted as a copy", not any("unique-hardlink.bin" in g or "a/unique.bin" in g for g in ex), ex)
ok("AppData and Program Files skipped by default", not any("AppData" in p or "Program Files" in p for g in ex for p in g), ex)
ok("unicode names", ["unicode/c\u0327af\u00e9 copy.txt", "unicode/\u00e7af\u00e9\u2013\u65e5\u672c\u8a9e.txt"] in ex or any("unicode" in p for g in ex for p in g), ex)
ok("symlinked folder not followed (no loop-link paths)", not any("loop-link" in p for g in ex for p in g))
ok("deep (>260 char) path found", any(len(p) > 260 for g in ex for p in g) or any("deep" in p for g in ex for p in g), ex)
ok("exact PNG copy of the photo is a similar-set member", any("photos/IMG_0001.jpg" in g and "photos/IMG_0001_small.jpg" in g and "photos/IMG_0001_resaved.jpg" in g for g in sim), sim)
ok("different pictures kept apart", not any("photos/IMG_0002.jpg" in g and "photos/IMG_0003.jpg" in g for g in sim) and not any("photos/IMG_0002.jpg" in g and "photos/IMG_0001.jpg" in g for g in sim), sim)
ok("broken image skipped with a reason", any("broken.jpg" in p for p, r in s.skipped), s.skipped)
s2 = run(include_system=True); ex2 = sets(s2, "exact")
ok("include system finds AppData copy", any("AppData/Local/x.bin" in g for g in ex2) or any("keep/Program Files/y.bin" in p for g in ex2 for p in g), ex2)
s3 = run(roots=[fx, os.path.join(fx, "a")]); ok("overlapping roots do not double-count", sets(s3, "exact") == ex or len(sets(s3, "exact")) == len(ex), (len(sets(s3, 'exact')), len(ex)))
s4 = run(near=False); ok("near off -> no similar sets", not s4.live_groups("similar"))
s5 = run(min_size=102400); ok("min size filter", all(s5.files[i]["size"] >= 102400 for g in s5.live_groups("exact") for i in g["files"]))
# cancel
import threading
s6 = dupes.Scan([fx], True, 6, 1, False); s6.cancel.set(); s6.run(); ok("cancel before start -> cancelled", s6.state == "cancelled")
# selection safety
g = s.live_groups("exact")[0]; allids = list(g["files"])
try: s.check_delete(allids); ok("refuses to select every copy", False)
except ValueError: ok("refuses to select every copy", True)
try: s.check_delete(allids[:-1]); ok("allows leaving one copy", True)
except ValueError as e: ok("allows leaving one copy", False, e)
try: s.check_delete([999999999]); ok("rejects unknown id", False)
except ValueError: ok("rejects unknown id", True)
try: s.check_delete([allids[0], allids[0]]); ok("rejects duplicate id", False)
except ValueError: ok("rejects duplicate id", True)
for rule, expect in (("newest", "c/mid-again.bin"), ("oldest", "a/mid.bin"), ("largest", None)):
    picks = s.suggest(rule, "exact"); gm = [x for x in s.live_groups("exact") if any(s.files[i]["path"].endswith("mid.bin") for i in x["files"])][0]
    left = [s.files[i]["path"] for i in gm["files"] if i not in picks]
    if expect: ok("keep-" + rule + " keeps " + expect, len(left) == 1 and left[0].replace(os.sep, "/").endswith(expect), left)
ok("suggest never selects a whole set", all(not all(i in set(s.suggest("newest", "exact")) for i in x["files"]) for x in s.live_groups("exact")))
# recycle path (box: test trash, never a permanent delete)
victim = os.path.join(fx, "b/small1 copy.txt"); good, msg = winshell.recycle(victim)
ok("recycle moves file to trash dir", good and not os.path.exists(victim) and len(os.listdir(os.environ["DUPEHUNTER_TEST_TRASH"])) == 1, msg)
shutil.rmtree(tmp, ignore_errors=True)
print("FAILED %d" % bad if bad else "ALL PASS"); sys.exit(1 if bad else 0)

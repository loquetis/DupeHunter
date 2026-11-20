"""DupeHunter routes. Deleting only ever goes through winshell.recycle() (Recycle Bin), after an explicit
confirm flag, for file ids that belong to the current scan results."""
import csv
import io
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime

import appserver
import dupes
import storage
import winshell
from appserver import BadRequest, NotFound

kit = appserver.Kit("DupeHunter", "DUPEHUNTER_PORT", 8768)
HOST, PORT = appserver.HOST, kit.port
last_seen = kit.last_seen
build_server = kit.build_server

SETTINGS = storage.Settings()
_scan = None
_scan_lock = threading.Lock()
_thumbs = OrderedDict()
_thumb_sem = threading.BoundedSemaphore(4)
_delete_lock = threading.Lock()
_last_delete = None
VERSION = "1.0.0"


def on_start():
    pass


def on_stop():
    if _scan and _scan.state == "running":
        _scan.cancel.set()


def _current():
    if _scan is None:
        raise NotFound("no scan yet")
    return _scan


def _clean_roots(roots):
    out, seen = [], set()
    for r in roots:
        if not isinstance(r, str) or not r.strip():
            continue
        p = os.path.abspath(r.strip().strip('"'))
        if not os.path.isdir(winshell.long_path(p)):
            raise BadRequest("Not a folder: " + r)
        k = dupes.norm(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


@kit.get("/api/status")
def status(_q):
    s = _scan
    base = {"version": VERSION, "now": time.time(), "settings": SETTINGS.get(),
            "capabilities": {"images": dupes.HAVE_PIL, "heic": dupes.HEIC, "windows": winshell.IS_WIN,
                             "min_sizes": list(storage.MIN_SIZES), "similarity": list(storage.SIMILARITY)},
            "drives": winshell.fixed_drives(), "folders": winshell.known_folders(), "last_delete": _last_delete}
    if s is None:
        base["scan"] = None
        return base
    base["scan"] = {"id": s.id, "state": s.state, "phase": s.phase, "done": s.done, "total": s.total, "error": s.error,
                    "files_seen": s.files_seen, "bytes_seen": s.bytes_seen, "started": s.started, "finished": s.finished,
                    "elapsed": (s.finished or time.time()) - s.started, "skipped_count": s.skipped_count,
                    "roots": s.roots, "near": s.near, "notes": s.notes}
    if s.state in ("done", "cancelled"):
        base["scan"]["summary"] = s.summary()
    return base


@kit.post("/api/settings")
def settings(d):
    try:
        return {"ok": True, "settings": SETTINGS.update(d)}
    except ValueError as e:
        raise BadRequest(str(e)) from None


@kit.post("/api/scan")
def start_scan(d):
    global _scan
    with _scan_lock:
        if _scan is not None and _scan.state == "running":
            raise BadRequest("A scan is already running")
        upd = {k: d[k] for k in ("roots", "near", "similarity", "min_size", "include_system") if k in d}
        try:
            storage.Settings.clean(upd)                      # validate first: a bad request must not be saved
            if "roots" in upd:
                upd["roots"] = _clean_roots(upd["roots"])
        except ValueError as e:
            raise BadRequest(str(e)) from None
        if upd:
            SETTINGS.update(upd)
        cfg = SETTINGS.get()
        roots = _clean_roots(cfg["roots"])
        if not roots:
            raise BadRequest("Add at least one folder or drive to scan")
        _thumbs.clear()
        s = dupes.Scan(roots, cfg["near"], cfg["similarity"], cfg["min_size"], cfg["include_system"])
        _scan = s
        threading.Thread(target=s.run, name="scan", daemon=True).start()
    return {"ok": True, "id": s.id}


@kit.post("/api/cancel")
def cancel(_d):
    if _scan and _scan.state == "running":
        _scan.cancel.set()
        return {"ok": True}
    return {"ok": False}


@kit.post("/api/pick-folder")
def pick(_d):
    if not winshell.IS_WIN:
        return {"ok": False, "error": "The folder picker is only available on Windows. Type a path instead."}
    path = winshell.pick_folder()
    return {"ok": bool(path), "path": path}


@kit.get("/api/groups")
def groups(q):
    s = _current()
    kind = q.get("kind") or "exact"
    if kind not in ("exact", "similar"):
        raise BadRequest("kind must be exact or similar")
    sort = q.get("sort") or "reclaimable"
    try:
        offset = max(0, int(q.get("offset") or 0))
        limit = min(200, max(1, int(q.get("limit") or 30)))
    except ValueError:
        raise BadRequest("offset and limit must be numbers") from None
    needle = (q.get("filter") or "").lower()
    views = [s.group_view(g) for g in s.live_groups(kind)]
    if needle:
        views = [v for v in views if any(needle in f["path"].lower() for f in v["files"])]
    key = {"reclaimable": lambda v: -v["reclaimable"], "size": lambda v: -v["size"], "count": lambda v: -v["count"],
           "name": lambda v: v["files"][0]["name"].lower()}.get(sort)
    if key is None:
        raise BadRequest("unknown sort")
    views.sort(key=key)
    return {"scan": s.id, "total": len(views), "groups": views[offset:offset + limit]}


@kit.get("/api/skipped")
def skipped(_q):
    s = _current()
    return {"count": s.skipped_count, "items": [{"path": p, "reason": r} for p, r in s.skipped]}


@kit.post("/api/suggest")
def suggest(d):
    s = _current()
    kind = d.get("kind") or "exact"
    if kind not in ("exact", "similar"):
        raise BadRequest("kind must be exact or similar")
    try:
        ids = s.suggest(d.get("rule"), kind)
    except ValueError as e:
        raise BadRequest(str(e)) from None
    return {"ids": ids, "items": [[i, s.files[i]["size"]] for i in ids], "scan": s.id,
            "bytes": sum(s.files[i]["size"] for i in ids)}


def _ids(d):
    ids = d.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 20000:
        raise BadRequest("'ids' must be a non-empty list of file ids")
    return ids


@kit.post("/api/preview-delete")
def preview(d):
    s = _current()
    try:
        recs = s.check_delete(_ids(d))
    except ValueError as e:
        raise BadRequest(str(e)) from None
    return {"ok": True, "count": len(recs), "bytes": sum(r["size"] for r in recs),
            "sample": [r["path"] for r in recs[:8]]}


@kit.post("/api/delete")
def delete(d):
    """Move the selected files to the Recycle Bin. Requires confirm == 'recycle' (the UI sends it only after
    its confirmation dialog) and the same safety check as the preview."""
    global _last_delete
    s = _current()
    if d.get("confirm") != "recycle":
        raise BadRequest("Deleting needs explicit confirmation")
    if s.state == "running":
        raise BadRequest("Wait for the scan to finish first")
    if d.get("scan") != s.id:
        raise BadRequest("These results are from an older scan. Scan again.")
    if not _delete_lock.acquire(blocking=False):
        raise BadRequest("Another delete is in progress")
    try:
        try:
            recs = s.check_delete(_ids(d))
        except ValueError as e:
            raise BadRequest(str(e)) from None
        done, failed, freed = [], [], 0
        for r in recs:
            p = r["path"]
            lp = winshell.long_path(p)
            try:
                st = os.stat(lp)
            except OSError:
                failed.append({"path": p, "reason": "already gone"})
                s.drop(r["id"])
                continue
            if st.st_size != r["size"] or abs(st.st_mtime - r["mtime"]) > 2:
                failed.append({"path": p, "reason": "changed since the scan, left alone"})
                continue
            # a group must still keep a copy that really exists on disk
            if not _keeper_exists(s, r):
                failed.append({"path": p, "reason": "the copy you are keeping is no longer on disk, left alone"})
                continue
            try:
                ok, msg = winshell.recycle(p)
            except OSError as e:
                ok, msg = False, str(e)
            if ok:
                freed += r["size"]
                done.append(p)
                s.drop(r["id"])
            else:
                failed.append({"path": p, "reason": msg})
        _last_delete = {"at": time.time(), "recycled": len(done), "failed": len(failed), "freed": freed}
        return {"ok": True, "recycled": len(done), "failed": failed[:100], "failed_count": len(failed), "freed": freed,
                "summary": s.summary()}
    finally:
        _delete_lock.release()


def _keeper_exists(s, rec):
    for g in s.live_groups():
        if rec["id"] not in g["files"]:
            continue
        others = [s.files[i] for i in g["files"] if i in s.files and i != rec["id"]]
        if not any(os.path.exists(winshell.long_path(o["path"])) for o in others):
            return False
    return True


@kit.post("/api/reveal")
def reveal(d):
    s = _current()
    f = s.files.get(d.get("id"))
    if not f:
        raise NotFound("unknown file")
    return {"ok": winshell.reveal(f["path"])}


@kit.raw("/api/thumb")
def thumb(q):
    s = _scan
    try:
        fid = int(q.get("id") or 0)
    except ValueError:
        fid = 0
    f = s.files.get(fid) if s else None
    if not f or not f["image"]:
        return 404, b'{"error":"not found"}', "application/json", {}
    data = _thumbs.get(fid)
    if data is None:
        with _thumb_sem:
            try:
                data = dupes.thumb_bytes(f["path"])
            except Exception:  # noqa: BLE001
                data = None
        if data is None:
            return 404, b'{"error":"no preview"}', "application/json", {}
        _thumbs[fid] = data
        while len(_thumbs) > 400:
            _thumbs.popitem(last=False)
    return 200, data, "image/jpeg", {"Cache-Control": "private, max-age=300"}


def _cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@kit.raw("/api/export.csv")
def export(_q):
    s = _current()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["set", "type", "path", "size_bytes", "modified", "width", "height", "set_reclaimable_bytes"])
    for g in s.live_groups():
        v = s.group_view(g)
        for f in v["files"]:
            w.writerow([g["id"], g["kind"], _cell(f["path"]), f["size"],
                        datetime.fromtimestamp(f["mtime"]).isoformat(timespec="seconds"), f["w"] or "", f["h"] or "", v["reclaimable"]])
    name = datetime.now().strftime("dupehunter-%Y%m%d-%H%M.csv")
    return 200, ("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", \
        {"Content-Disposition": f'attachment; filename="{name}"'}

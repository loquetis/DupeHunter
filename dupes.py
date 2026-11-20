"""DupeHunter engine: folder walk -> size groups -> partial hash -> full hash, plus near-duplicate photos
(perceptual dHash). Read-only: nothing here modifies a file."""
import hashlib
import itertools
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import winshell

try:
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 120_000_000
    HAVE_PIL = True
except Exception:  # noqa: BLE001
    HAVE_PIL = False
HEIC = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC = True
except Exception:  # noqa: BLE001
    pass

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
HEIC_EXT = {".heic", ".heif"}
PARTIAL = 64 * 1024
CHUNK = 1024 * 1024
SYSTEM_DIRS = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "system volume information",
               "appdata", "$windows.~bt", "$windows.~ws", "windows.old", "recovery", "msocache", "config.msi"}
MAX_SKIPPED_SHOWN = 200
CLOUD_ATTRS = 0x1000 | 0x40000 | 0x400000      # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
_ids = itertools.count(1)            # global so a stale page can never name a file from a newer scan


def is_image_ext(ext):
    return ext in IMAGE_EXT or (HEIC and ext in HEIC_EXT)


def norm(p):
    return os.path.normcase(os.path.abspath(p))


class Cancelled(Exception):
    pass


class Scan:
    """Holds the state of one scan and (afterwards) its results."""

    def __init__(self, roots, near, similarity, min_size, include_system):
        self.id = next(_ids)
        self.roots = roots
        self.near = near and HAVE_PIL
        self.similarity = similarity
        self.min_size = max(1, int(min_size))      # empty files are never "duplicates"
        self.include_system = include_system
        self.cancel = threading.Event()
        self.state = "running"                     # running | done | cancelled | error
        self.phase = "Listing files"
        self.done = 0
        self.total = 0
        self.error = None
        self.started = time.time()
        self.finished = None
        self.skipped = []                          # (path, reason)
        self.skipped_count = 0
        self.files_seen = 0
        self.bytes_seen = 0
        self.groups = []                           # exact + similar group dicts
        self.files = {}                            # file id -> record
        self.by_path = {}
        self.lock = threading.Lock()
        self.notes = []

    # ---- progress helpers
    def _skip(self, path, reason):
        self.skipped_count += 1
        if len(self.skipped) < MAX_SKIPPED_SHOWN:
            self.skipped.append((path, reason))

    def _check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _phase(self, name, total):
        self.phase, self.done, self.total = name, 0, total

    # ---- steps
    def walk(self):
        seen_dirs, seen_files = set(), set()
        by_size = {}
        for root in self.roots:
            stack = [root]
            while stack:
                self._check()
                d = stack.pop()
                key = norm(d)
                if key in seen_dirs:
                    continue
                seen_dirs.add(key)
                try:
                    with os.scandir(winshell.long_path(d)) as it:
                        entries = list(it)
                except OSError as e:
                    self._skip(d, e.strerror or "cannot open folder")
                    continue
                for e in entries:
                    try:
                        name = e.name
                        full = os.path.join(d, name)
                        if e.is_symlink():
                            continue                       # never follow links/junctions: avoids loops and double counting
                        if e.is_dir(follow_symlinks=False):
                            if not self.include_system and name.lower() in SYSTEM_DIRS:
                                continue
                            if getattr(e, "is_junction", lambda: False)():         # Windows junction: same loop/double-count risk as a link
                                continue
                            stack.append(full)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            if st.st_size < self.min_size:
                                continue
                            if getattr(st, "st_file_attributes", 0) & CLOUD_ATTRS:
                                self._skip(full, "online-only cloud file (OneDrive etc.): not downloaded")
                                continue                    # hashing it would make Windows download it
                            fk = norm(full)
                            if fk in seen_files:
                                continue                    # overlapping roots
                            seen_files.add(fk)
                            self.files_seen += 1
                            self.bytes_seen += st.st_size
                            by_size.setdefault(st.st_size, []).append((full, st.st_size, st.st_mtime))
                    except OSError as ex:
                        self._skip(os.path.join(d, e.name), ex.strerror or "unreadable")
                self.done = self.files_seen
        return by_size

    def _drop_hardlinks(self, items):
        """Hard links to one file are not copies (deleting one frees nothing). DirEntry.stat() on Windows has no
        file index, so look it up with a real stat, only for files that already share a size."""
        out, seen = [], set()
        for it in items:
            try:
                st = os.stat(winshell.long_path(it[0]))
                key = (st.st_dev, st.st_ino) if st.st_ino else None
            except OSError as e:
                self._skip(it[0], e.strerror or "unreadable")
                continue
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            out.append(it)
        return out if len(out) > 1 else []

    @staticmethod
    def _hash_file(path, size, partial):
        h = hashlib.blake2b(digest_size=20)
        with open(winshell.long_path(path), "rb") as f:
            if partial:
                h.update(f.read(PARTIAL))
                if size > PARTIAL:                       # tail too (for size <= 2*PARTIAL head+tail cover the whole file)
                    f.seek(max(PARTIAL, size - PARTIAL))
                    h.update(f.read(PARTIAL))
                h.update(str(size).encode())
            else:
                while True:
                    b = f.read(CHUNK)
                    if not b:
                        break
                    h.update(b)
        return h.hexdigest()

    def _hash_many(self, items, partial):
        """items: list of (path,size,mtime). Returns {hash: [item,...]} (only hashes with >1 file)."""
        out = {}
        pool = ThreadPoolExecutor(max_workers=4)
        try:
            def work(it):
                if self.cancel.is_set():
                    return it, None, "cancelled"
                try:
                    return it, self._hash_file(it[0], it[1], partial), None
                except OSError as e:
                    return it, None, e.strerror or "unreadable"
            for it, h, err in pool.map(work, items):
                self.done += 1
                self._check()
                if err:
                    self._skip(it[0], err)
                    continue
                out.setdefault((it[1], h), []).append(it)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return {k: v for k, v in out.items() if len(v) > 1}

    def _record(self, item, kind_group):
        path, size, mtime = item
        key = norm(path)
        if key in self.by_path:                  # one record per path, shared by the exact and the similar group
            return self.by_path[key]
        fid = next(_ids)
        ext = os.path.splitext(path)[1].lower()
        rec = {"id": fid, "path": path, "size": size, "mtime": mtime, "image": is_image_ext(ext), "group": kind_group}
        self.files[fid] = rec
        self.by_path[key] = rec
        return rec

    def run(self):
        try:
            by_size = self.walk()
            cands = []
            for items in by_size.values():
                if len(items) > 1:
                    cands.extend(self._drop_hardlinks(items))
            self._check()
            self._phase("Comparing file starts", len(cands))
            partial = self._hash_many(cands, partial=True)
            cands2 = [it for items in partial.values() for it in items]
            need_full = [it for it in cands2 if it[1] > 2 * PARTIAL]
            small = [it for it in cands2 if it[1] <= 2 * PARTIAL]      # partial hash already covered the whole file
            self._phase("Confirming matches", len(need_full))
            full = self._hash_many(need_full, partial=False)
            exact = [v for v in full.values()]
            for (size, _h), items in partial.items():
                if size <= 2 * PARTIAL:
                    exact.append(items)
            del small
            gid = 0
            exact_files = set()
            clusters = []
            for items in sorted(exact, key=lambda v: -(v[0][1] * (len(v) - 1))):
                gid += 1
                recs = [self._record(i, gid) for i in sorted(items, key=lambda i: i[0].lower())]
                g = {"id": gid, "kind": "exact", "size": recs[0]["size"], "files": [r["id"] for r in recs]}
                self.groups.append(g)
                clusters.append(recs)
                exact_files.update(r["id"] for r in recs)
            if self.near:
                self.near_scan(by_size, clusters, gid)
            self.state = "done"
        except Cancelled:
            self.state = "cancelled"
        except Exception as e:  # noqa: BLE001
            self.state = "error"
            self.error = str(e)
        finally:
            self.finished = time.time()

    # ---- near duplicates
    @staticmethod
    def dhash(path):
        """64-bit difference hash + a vertical one for confirmation. Returns (h, v, w, h_px) or None."""
        with Image.open(winshell.long_path(path)) as im:
            w, hh = im.size
            if min(w, hh) < 24:
                return None
            try:
                im.draft("L", (256, 256))            # fast JPEG downscale on decode
            except Exception:  # noqa: BLE001
                pass
            im = ImageOps.exif_transpose(im)
            g = im.convert("L")
            a = g.resize((9, 8), Image.LANCZOS)
            b = g.resize((8, 9), Image.LANCZOS)
            pa, pb = list(a.getdata()), list(b.getdata())
        hv = 0
        for row in range(8):
            for col in range(8):
                hv = (hv << 1) | (1 if pa[row * 9 + col] > pa[row * 9 + col + 1] else 0)
        vv = 0
        for row in range(8):
            for col in range(8):
                vv = (vv << 1) | (1 if pb[row * 8 + col] > pb[(row + 1) * 8 + col] else 0)
        return hv, vv, w, hh

    def near_scan(self, by_size, clusters, gid):
        imgs = [it for items in by_size.values() for it in items if is_image_ext(os.path.splitext(it[0])[1].lower())]
        cluster_of = {}
        for recs in clusters:
            for r in recs:
                cluster_of[norm(r["path"])] = recs
        # one representative per exact cluster, so identical copies are not hashed again and again
        reps, rep_seen = [], set()
        for it in imgs:
            cl = cluster_of.get(norm(it[0]))
            if cl is not None:
                if id(cl) in rep_seen:
                    continue
                rep_seen.add(id(cl))
            reps.append(it)
        self._phase("Looking for similar photos", len(reps))
        hashed = []
        pool = ThreadPoolExecutor(max_workers=3)
        try:
            def work(it):
                if self.cancel.is_set():
                    return it, None, "cancelled"
                try:
                    return it, self.dhash(it[0]), None
                except Exception as e:  # noqa: BLE001 (corrupt image, unsupported format, decompression bomb ...)
                    return it, None, str(e)[:80]
            for it, hh, err in pool.map(work, reps):
                self.done += 1
                self._check()
                if err:
                    self._skip(it[0], "image not readable: " + err)
                elif hh:
                    hashed.append((it, hh))
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        # Group identical hashes, then bucket by chunks (pigeonhole: <= t differing bits means some
        # one of t+1 chunks is identical), compare candidate pairs, union-find into sets.
        t = self.similarity
        n = len(hashed)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        by_hash = {}
        for i, (_, hh) in enumerate(hashed):
            by_hash.setdefault(hh[0], []).append(i)
        for idxs in by_hash.values():           # identical dHash -> candidates; confirm with the vertical hash
            for j in idxs[1:]:
                if bin(hashed[idxs[0]][1][1] ^ hashed[j][1][1]).count("1") <= t + 6:
                    parent[find(j)] = find(idxs[0])
        uniq = list(by_hash.keys())
        nchunks = t + 1
        bits = [64 // nchunks + (1 if i < 64 % nchunks else 0) for i in range(nchunks)]
        buckets = {}
        for u, hv in enumerate(uniq):
            shift = 0
            for ci, b in enumerate(bits):
                buckets.setdefault((ci, (hv >> shift) & ((1 << b) - 1)), []).append(u)
                shift += b
        compared = set()
        capped = False
        for members in buckets.values():
            if len(members) > 1500:
                members = members[:1500]        # flat/blank images all hash alike; cap keeps the scan bounded
                capped = True
            for a in range(len(members)):
                self._check()
                for b in range(a + 1, len(members)):
                    ua, ub = members[a], members[b]
                    key = (ua, ub) if ua < ub else (ub, ua)
                    if key in compared:
                        continue
                    compared.add(key)
                    if (uniq[ua] ^ uniq[ub]).bit_count() <= t:
                        ia, ib = by_hash[uniq[ua]][0], by_hash[uniq[ub]][0]
                        if (hashed[ia][1][1] ^ hashed[ib][1][1]).bit_count() <= t + 6:
                            parent[find(ia)] = find(ib)
        if capped:
            self.notes.append("Some very plain images look alike to the hasher; those were only partly compared.")
        sets = {}
        for i in range(n):
            sets.setdefault(find(i), []).append(i)
        for members in sets.values():
            if len(members) < 2:
                continue
            gid += 1
            recs = []
            for i in sorted(members, key=lambda i: hashed[i][0][0].lower()):
                it, hh = hashed[i]
                cl = cluster_of.get(norm(it[0]))
                for item in ([(r["path"], r["size"], r["mtime"]) for r in cl] if cl else [it]):
                    rec = self._record(item, gid)
                    rec["w"], rec["h"] = hh[2], hh[3]
                    if rec not in recs:
                        recs.append(rec)
            size = max(r["size"] for r in recs)
            self.groups.append({"id": gid, "kind": "similar", "size": size, "files": [r["id"] for r in recs]})

    # ---- results
    def group_view(self, g):
        files = [self.files[i] for i in g["files"] if i in self.files]
        n = len(files)
        total = sum(f["size"] for f in files)
        if g["kind"] == "exact":
            reclaim = total - max(f["size"] for f in files) if n else 0
        else:
            reclaim = total - max(f["size"] for f in files) if n else 0
        return {"id": g["id"], "kind": g["kind"], "count": n, "size": g["size"], "total": total, "reclaimable": reclaim,
                "files": [{"id": f["id"], "path": f["path"], "dir": os.path.dirname(f["path"]), "name": os.path.basename(f["path"]),
                           "size": f["size"], "mtime": f["mtime"], "image": f["image"], "w": f.get("w"), "h": f.get("h")} for f in files]}

    def live_groups(self, kind=None):
        out = []
        for g in self.groups:
            if kind and g["kind"] != kind:
                continue
            live = [i for i in g["files"] if i in self.files]
            if len(live) >= 2:
                out.append(g)
        return out

    def summary(self):
        ex = self.live_groups("exact")
        si = self.live_groups("similar")

        def tot(gs):
            r = 0
            for g in gs:
                fs = [self.files[i]["size"] for i in g["files"] if i in self.files]
                r += sum(fs) - max(fs)
            return r
        return {"exact_groups": len(ex), "exact_files": sum(len([i for i in g["files"] if i in self.files]) for g in ex),
                "exact_reclaimable": tot(ex), "similar_groups": len(si),
                "similar_files": sum(len([i for i in g["files"] if i in self.files]) for g in si), "similar_reclaimable": tot(si)}

    def suggest(self, rule, kind):
        """File ids to REMOVE so that one file per group is kept, per `rule`. Selection only; deletes nothing."""
        picks = []
        for g in self.live_groups(kind):
            fs = [self.files[i] for i in g["files"] if i in self.files]
            if rule == "newest":
                keep = max(fs, key=lambda f: (f["mtime"], -len(f["path"])))
            elif rule == "oldest":
                keep = min(fs, key=lambda f: (f["mtime"], len(f["path"])))
            elif rule == "largest":
                keep = max(fs, key=lambda f: (f["size"], f["mtime"]))
            elif rule == "smallest":
                keep = min(fs, key=lambda f: (f["size"], -f["mtime"]))
            elif rule == "shortest":
                keep = min(fs, key=lambda f: (len(f["path"]), f["path"].lower()))
            else:
                raise ValueError("unknown rule")
            picks.extend(f["id"] for f in fs if f is not keep)
        return picks

    def check_delete(self, ids):
        """Raises ValueError if the selection is unsafe. Returns the file records."""
        recs, sel = [], set()
        for i in ids:
            if not isinstance(i, int) or isinstance(i, bool) or i in sel:
                raise ValueError("bad file id")
            if i not in self.files:
                raise ValueError("a selected file is not part of the current results (scan again)")
            sel.add(i)
            recs.append(self.files[i])
        for g in self.live_groups():
            members = [i for i in g["files"] if i in self.files]
            if any(i in sel for i in members) and all(i in sel for i in members):
                raise ValueError("every file in one set is selected: at least one copy in each set must be kept")
        return recs

    def drop(self, fid):
        self.files.pop(fid, None)


def thumb_bytes(path, size=176):
    if not HAVE_PIL:
        return None
    with Image.open(winshell.long_path(path)) as im:
        try:
            im.draft("RGB", (size * 2, size * 2))
        except Exception:  # noqa: BLE001
            pass
        im = ImageOps.exif_transpose(im)
        im.thumbnail((size, size))
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (32, 34, 38))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[3])
            im = bg
        buf = BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=78)
        return buf.getvalue()

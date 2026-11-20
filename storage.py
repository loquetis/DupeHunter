"""DupeHunter settings (JSON in %APPDATA%\\DupeHunter)."""
import json
import os
import threading

APP = "DupeHunter"
DATA_DIR = os.environ.get("DUPEHUNTER_DATA") or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.local/share"), APP)
DEFAULTS = {"roots": [], "near": True, "similarity": 6, "min_size": 1024, "include_system": False}
MIN_SIZES = (1, 1024, 102400, 1048576)
SIMILARITY = (3, 6, 10)       # max differing bits out of 64: strict / normal / loose


class Settings:
    def __init__(self):
        self.path = os.path.join(DATA_DIR, "settings.json")
        self.lock = threading.Lock()
        self.data = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                self.data.update(self.clean(saved, strict=False))
        except (OSError, ValueError):
            pass

    @staticmethod
    def clean(d, strict=True):
        out = {}
        if "roots" in d:
            r = d["roots"]
            if not isinstance(r, list) or len(r) > 50 or not all(isinstance(x, str) and 0 < len(x) < 1000 for x in r):
                if strict:
                    raise ValueError("roots must be a list of up to 50 folder paths")
            else:
                out["roots"] = r
        if "near" in d:
            out["near"] = bool(d["near"])
        if "include_system" in d:
            out["include_system"] = bool(d["include_system"])
        if "similarity" in d:
            if d["similarity"] in SIMILARITY:
                out["similarity"] = d["similarity"]
            elif strict:
                raise ValueError("similarity must be one of %s" % (SIMILARITY,))
        if "min_size" in d:
            if d["min_size"] in MIN_SIZES:
                out["min_size"] = d["min_size"]
            elif strict:
                raise ValueError("min_size must be one of %s" % (MIN_SIZES,))
        return out

    def update(self, d):
        c = self.clean(d)
        with self.lock:
            self.data.update(c)
            self.save()
        return dict(self.data)

    def save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def get(self):
        with self.lock:
            return dict(self.data)

"""Windows-only helpers via ctypes: Recycle Bin delete, drive types, folder picker, reveal in Explorer.
On other systems (the box test environment) delete moves files into a local test-trash folder instead:
there is no code path anywhere that permanently deletes a user file."""
import os
import shutil
import subprocess
import sys
import time

IS_WIN = os.name == "nt"


def long_path(p):
    """Prefix with \\\\?\\ on Windows so paths over 260 chars work."""
    if not IS_WIN:
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def strip_long(p):
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    if p.startswith("\\\\?\\"):
        return p[4:]
    return p


def drive_type(path):
    """2 removable, 3 fixed, 4 network, 5 cdrom, 6 ramdisk, 0 unknown. Non-Windows: 3."""
    if not IS_WIN:
        return 3
    import ctypes
    p = strip_long(os.path.abspath(path))
    if p.startswith("\\\\"):
        return 4
    root = os.path.splitdrive(p)[0] + "\\"
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def fixed_drives():
    if not IS_WIN:
        return []
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    out = []
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(65 + i)}:\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                out.append(root)
    return out


def known_folders():
    home = os.path.expanduser("~")
    names = ["Pictures", "Documents", "Downloads", "Desktop", "Videos", "Music"]
    return [{"name": n, "path": os.path.join(home, n)} for n in names if os.path.isdir(os.path.join(home, n))]


if IS_WIN:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_SILENT = 0x4
    FOF_NOCONFIRMATION = 0x10
    FOF_ALLOWUNDO = 0x40
    FOF_NOERRORUI = 0x400
    FOF_WANTNUKEWARNING = 0x4000


def recycle(path):
    """Send ONE file to the Recycle Bin. Returns (ok, message). Never deletes permanently:
    ALLOWUNDO is always set, and WANTNUKEWARNING makes Windows ask (instead of silently deleting) if it
    cannot recycle; non-fixed drives are refused up front because they usually have no Recycle Bin."""
    if drive_type(path) != 3:
        return False, "not on a fixed drive (no Recycle Bin there), left alone"
    if not IS_WIN:
        dest_dir = os.path.join(os.environ.get("DUPEHUNTER_TEST_TRASH") or os.path.join(os.path.expanduser("~"), ".dupehunter-test-trash"))
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "%d-%s" % (time.time_ns(), os.path.basename(path)))
        shutil.move(path, dest)
        return True, "moved to test trash"
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = strip_long(os.path.abspath(path)) + "\0"      # the shell API does not take \\?\ paths reliably
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_WANTNUKEWARNING | FOF_NOERRORUI | FOF_SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        return False, "Windows could not recycle it (code 0x%X)" % rc
    if op.fAnyOperationsAborted:
        return False, "cancelled by Windows"
    if os.path.exists(long_path(path)):
        return False, "file is still there after the request"
    return True, "sent to Recycle Bin"


def reveal(path):
    if not IS_WIN:
        return False
    p = strip_long(os.path.abspath(path))
    if not os.path.exists(long_path(p)):
        p = os.path.dirname(p)
    subprocess.Popen(["explorer.exe", "/select," + p], close_fds=True)
    return True


def pick_folder(title="Choose a folder to scan"):
    """Native folder dialog without tkinter. Returns a path or None."""
    if not IS_WIN:
        return None
    ole32, shell32 = ctypes.windll.ole32, ctypes.windll.shell32

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [("hwndOwner", wintypes.HWND), ("pidlRoot", ctypes.c_void_p), ("pszDisplayName", wintypes.LPWSTR),
                    ("lpszTitle", wintypes.LPCWSTR), ("ulFlags", wintypes.UINT), ("lpfn", ctypes.c_void_p),
                    ("lParam", wintypes.LPARAM), ("iImage", ctypes.c_int)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.OleInitialize(None)
    try:
        buf = ctypes.create_unicode_buffer(1024)
        bi = BROWSEINFOW()
        bi.pszDisplayName = ctypes.cast(buf, wintypes.LPWSTR)
        bi.lpszTitle = title
        bi.ulFlags = 0x1 | 0x40 | 0x10     # RETURNONLYFSDIRS | NEWDIALOGSTYLE | EDITBOX
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return None
        out = ctypes.create_unicode_buffer(1024)
        ok = shell32.SHGetPathFromIDListW(pidl, out)
        ole32.CoTaskMemFree(pidl)
        return out.value if ok else None
    finally:
        ole32.OleUninitialize()

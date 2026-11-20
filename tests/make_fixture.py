"""Build a small fixture folder with planted duplicates. usage: make_fixture.py DIR"""
import os, random, sys
from PIL import Image, ImageDraw, ImageFilter
root = sys.argv[1]
os.makedirs(root, exist_ok=True)
rnd = random.Random(7)
def blob(n, seed): return random.Random(seed).randbytes(n)
def put(rel, data):
    p = os.path.join(root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: f.write(data)
    return p
put("a/small1.txt", b"hello duplicate world " * 40); put("b/small1 copy.txt", b"hello duplicate world " * 40)
put("a/mid.bin", blob(100_000, 1)); put("b/mid.bin", blob(100_000, 1)); put("c/mid-again.bin", blob(100_000, 1))
big = blob(3_000_000, 2); put("a/big.bin", big); put("b/big.bin", big)
tweak = bytearray(big); tweak[1_500_000] ^= 0xFF; put("c/big-almost.bin", bytes(tweak))      # same size, same head/tail, different middle
mid_head_tail_same = bytearray(blob(100_000, 3)); other = bytearray(mid_head_tail_same); other[50_000] ^= 1
put("a/ht1.bin", bytes(mid_head_tail_same)); put("b/ht2.bin", bytes(other))                   # 100 KB: head+tail overlap must still catch the middle
put("a/empty1.dat", b""); put("b/empty2.dat", b"")                                               # empty files are never duplicates
put("a/unique.bin", blob(5000, 99))
put("AppData/Local/x.bin", blob(70_000, 5)); put("keep/Program Files/y.bin", blob(70_000, 5)); put("a/z.bin", blob(70_000, 5))
put("unicode/çafé–日本語.txt", b"unicode name " * 50); put("unicode/çafé copy.txt", b"unicode name " * 50)
deep = os.path.join(root, *["verylongfoldername%02d" % i for i in range(14)]); os.makedirs(deep, exist_ok=True)
put(os.path.join(deep, "deep.bin"), blob(70_000, 8)); put("b/deep-copy.bin", blob(70_000, 8))   # >260 char path on Windows
try:
    os.link(os.path.join(root, "a/unique.bin"), os.path.join(root, "b/unique-hardlink.bin"))     # hard link: not a duplicate
except OSError: pass
try: os.symlink(os.path.join(root, "a"), os.path.join(root, "loop-link"))                         # link to a folder: never followed
except OSError: pass
# images: original, resized copy, re-saved JPEG, a different picture, a broken file
def pic(seed, size=(640, 480)):
    r = random.Random(seed); im = Image.new("RGB", size, (r.randrange(256), r.randrange(256), r.randrange(256))); d = ImageDraw.Draw(im)
    for _ in range(14):
        x, y = r.randrange(size[0]), r.randrange(size[1]); d.ellipse((x, y, x + r.randrange(40, 200), y + r.randrange(40, 200)), fill=(r.randrange(256), r.randrange(256), r.randrange(256)))
    return im.filter(ImageFilter.GaussianBlur(2))
orig = pic(1); os.makedirs(os.path.join(root, "photos"), exist_ok=True)
orig.save(os.path.join(root, "photos/IMG_0001.jpg"), quality=92)
orig.resize((320, 240), Image.LANCZOS).save(os.path.join(root, "photos/IMG_0001_small.jpg"), quality=85)
Image.open(os.path.join(root, "photos/IMG_0001.jpg")).save(os.path.join(root, "photos/IMG_0001_resaved.jpg"), quality=60)
orig.save(os.path.join(root, "photos/IMG_0001.png"))
pic(2).save(os.path.join(root, "photos/IMG_0002.jpg"), quality=90)
pic(3).save(os.path.join(root, "photos/IMG_0003.jpg"), quality=90)
put("photos/broken.jpg", b"\xff\xd8\xff\xe0 not really a jpeg" * 20)
os.utime(os.path.join(root, "a/mid.bin"), (1_600_000_000, 1_600_000_000)); os.utime(os.path.join(root, "b/mid.bin"), (1_650_000_000, 1_650_000_000)); os.utime(os.path.join(root, "c/mid-again.bin"), (1_700_000_000, 1_700_000_000))
print("fixture ready", root)

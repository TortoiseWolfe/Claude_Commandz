"""Hoshi's avatar: an original round badge, deep blue, a headset around a bold H with signal waves. Drawn at 4x, shrunk to 256 px."""
import sys
from PIL import Image, ImageDraw, ImageFont

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
BLUE, RING, WHITE, GLOW = (11, 36, 99), (74, 134, 232), (255, 255, 255), (140, 190, 255)

d.ellipse((8, 8, S - 8, S - 8), fill=RING)
d.ellipse((44, 44, S - 44, S - 44), fill=BLUE)

# headset band: a thick arc over the top, ear cups at both ends
cx, cy = S // 2, 500
r = 255
d.arc((cx - r, cy - r, cx + r, cy + r), start=180, end=360, fill=WHITE, width=46)
for side in (-1, 1):
    x = cx + side * r
    d.rounded_rectangle((x - 52, cy - 20, x + 52, cy + 190), radius=40, fill=WHITE)
# microphone boom from the left cup toward the mouth
d.line((cx - r, cy + 190, cx - r + 10, cy + 300, cx - 130, cy + 340), fill=WHITE, width=26, joint="curve")
d.ellipse((cx - 170, cy + 312, cx - 106, cy + 376), fill=GLOW)

# signal waves leaving the right ear cup
wx, wy = cx + r + 52, cy + 85
for i, rad in enumerate((55, 100, 145)):
    d.arc((wx - rad, wy - rad, wx + rad, wy + rad), start=-50, end=50, fill=GLOW, width=22 - 3 * i)

# the H
font = None
for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
    try:
        font = ImageFont.truetype(path, 380)
        break
    except OSError:
        pass
if font is None:
    sys.exit("no bold font found")
d.text((cx, cy + 90), "H", font=font, fill=WHITE, anchor="mm")

out = img.resize((256, 256), Image.LANCZOS)
out.save(sys.argv[1], optimize=True)
print(sys.argv[1])

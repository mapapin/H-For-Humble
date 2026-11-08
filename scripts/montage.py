"""MP4 montage of a finished audit: every perspective's replay side by side, step by step, with the agent's thought,
its click (and where a tremor click landed) and the outcome. Real frames from H's session history. For pitch videos.

Usage: .venv/bin/python scripts/montage.py <audit-id> [out.mp4]
Needs `pip install imageio imageio-ffmpeg` (bundled ffmpeg).
"""
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "poc"))
from app import store  # noqa: E402
from scoring import scorecard  # noqa: E402

W, H = 1920, 1080
TILE_W, TILE_H = 600, 452
SHOT_H = 322
FPS, STEP_FRAMES, END_FRAMES = 10, 13, 30
NAMES = {"baseline": "Baseline", "low_vision": "Low vision · 200% zoom + blur", "color_blind": "Colour blind · deuteranopia",
         "tremor": "Hand tremor · ±10 px", "keyboard_only": "Keyboard only · no mouse"}
INK, MUTED, PAPER, BG, ACCENT = (41, 42, 51), (107, 101, 115), (253, 252, 250), (246, 245, 241), (81, 73, 189)
GOOD, POOR = (37, 104, 74), (161, 54, 47)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def tile_frames(audit, run, base: Path):
    """[(screenshot, step, thought, action, effects)] for one run."""
    steps = (run.get("replay") or {}).get("steps") or []
    frames = (run.get("replay") or {}).get("frames") or []
    cache, out, last = {}, [], None
    for s in steps:
        f = s.get("frame")
        if f is not None and f < len(frames) and frames[f].get("file"):
            if f not in cache:
                try:
                    cache[f] = Image.open(base / frames[f]["file"]).convert("RGB")
                except OSError:
                    cache[f] = None
            last = cache[f] or last
        out.append((last, s))
    return out


def draw_tile(canvas, x, y, persona, shot, step, idx, total, outcome, f):
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((x, y, x + TILE_W, y + TILE_H), 16, fill=PAPER, outline=(232, 231, 226), width=2)
    d.text((x + 18, y + 14), NAMES.get(persona, persona), font=f["bold"], fill=INK)
    d.text((x + TILE_W - 18, y + 16), f"step {idx}/{total}", font=f["small"], fill=MUTED, anchor="ra")
    sx, sy, sw = x + 10, y + 48, TILE_W - 20
    if shot is not None:
        ratio = sw / shot.width
        img = shot.resize((sw, int(shot.height * ratio)))
        img = img.crop((0, 0, sw, min(SHOT_H, img.height)))
        canvas.paste(img, (sx, sy))
        a, e = (step or {}).get("action") or {}, (step or {}).get("effects") or {}
        if a.get("x") is not None and a.get("y") is not None:
            px, py = sx + a["x"] * sw, sy + a["y"] * shot.height * ratio
            if py < sy + SHOT_H:
                col = POOR if e.get("blocked") else ACCENT
                d.ellipse((px - 16, py - 16, px + 16, py + 16), outline=col, width=4)
                if e.get("jitter") and not e.get("blocked"):
                    hx, hy = px + e["jitter"][0] * ratio, py + e["jitter"][1] * ratio
                    d.line((px, py, hx, hy), fill=(255, 90, 54), width=2)
                    d.ellipse((hx - 7, hy - 7, hx + 7, hy + 7), fill=(255, 90, 54), outline="white", width=2)
        if e.get("blocked"):
            d.rounded_rectangle((sx + 10, sy + 10, sx + 300, sy + 40), 8, fill=POOR)
            d.text((sx + 20, sy + 16), "Mouse blocked", font=f["bold_small"], fill="white")
        if a.get("kind") == "keys" and a.get("keys"):
            names = {"return": "Enter", "enter": "Enter", "tab": "Tab", "space": "Space", "arrowdown": "↓", "down": "↓",
                     "arrowup": "↑", "up": "↑", "arrowright": "→", "right": "→", "arrowleft": "←", "left": "←", "shift": "Shift"}
            label = " + ".join(names.get(str(k).lower(), str(k).capitalize()) for k in a["keys"])
            d.rounded_rectangle((sx + sw / 2 - 70, sy + SHOT_H - 54, sx + sw / 2 + 70, sy + SHOT_H - 16), 8, fill=(31, 27, 44))
            d.text((sx + sw / 2, sy + SHOT_H - 35), label, font=f["bold_small"], fill="white", anchor="mm")
    thought = ((step or {}).get("thought") or "").replace("**", "").replace("`", "")
    lines = textwrap.wrap(thought, 70)[:3]
    for i, line in enumerate(lines):
        d.text((x + 18, y + 48 + SHOT_H + 10 + i * 22), line, font=f["small"], fill=INK)
    if outcome:
        ok = outcome == "Completed"
        d.rounded_rectangle((x + TILE_W - 190, y + 56, x + TILE_W - 20, y + 96), 20, fill=GOOD if ok else POOR)
        d.text((x + TILE_W - 105, y + 76), ("✓ " if ok else "✗ ") + outcome, font=f["bold_small"], fill="white", anchor="mm")


def main(audit_id: str, out: str | None = None):
    import imageio.v2 as imageio

    with store.connect() as db:
        audit = store.serialize(db.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone())
    base = store.DATA / "audits" / audit_id
    scores = audit.get("scores") or scorecard(audit["results"], audit["config"]["personas"])
    runs = {}
    for r in audit["results"]:
        if r.get("replay", {}).get("steps"):
            runs.setdefault(r["persona"], r)
    personas = [p for p in audit["config"]["personas"] if p in runs][:5]
    tracks = {p: tile_frames(audit, runs[p], base) for p in personas}
    longest = max(len(t) for t in tracks.values())
    f = {"bold": font(BOLD, 22), "small": font(FONT, 16), "bold_small": font(BOLD, 17), "title": font(SERIF, 40),
         "sub": font(FONT, 20)}
    left = (W - (3 * TILE_W + 2 * 20)) // 2
    positions = [(left + i * (TILE_W + 20), 116) for i in range(3)] + \
        [(left + (TILE_W + 20) // 2 + i * (TILE_W + 20), 116 + TILE_H + 18) for i in range(2)]
    target = Path(out or ROOT / "docs" / f"montage-{audit_id[:8]}.mp4")
    writer = imageio.get_writer(str(target), fps=FPS, codec="libx264", quality=7, macro_block_size=8)
    try:
        for k in range(longest + 1):
            canvas = Image.new("RGB", (W, H), BG)
            d = ImageDraw.Draw(canvas)
            d.text((left, 30), "humble.", font=f["title"], fill=ACCENT)
            d.text((left + 210, 46), f"{len(personas)} H agents · one journey · {audit['url']}", font=f["sub"], fill=MUTED)
            task = audit["config"]["tasks"][0]["title"]
            d.text((W - left, 46), task, font=f["sub"], fill=INK, anchor="ra")
            for (x, y), p in zip(positions, personas):
                track = tracks[p]
                i = min(k, len(track) - 1)
                shot, step = track[i]
                done = k >= len(track)
                per = (scores.get("perspectives") or {}).get(p) or {}
                outcome = ("Completed" if per.get("completion") == 1 else "Blocked") if done else None
                draw_tile(canvas, x, y, p, shot, step, i + 1, len(track), outcome, f)
            frames = END_FRAMES if k == longest else STEP_FRAMES
            arr = __import__("numpy").asarray(canvas)
            for _ in range(frames):
                writer.append_data(arr)
    finally:
        writer.close()
    print(f"{target} ({target.stat().st_size // 1024} KB, {longest} steps)")


if __name__ == "__main__":
    main(*sys.argv[1:])

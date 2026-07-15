"""Render a terminal-style animated GIF of a real gitmemory session.

Uses Pillow (no external recorder). The transcript below is the *actual* output
captured from a live run against google/gemma-4-e4b in LM Studio, trimmed to fit
a terminal frame. Produces docs/demo.gif.

Run:  python tools/render_demo_gif.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

# --- palette --------------------------------------------------------------- #
BG = (24, 26, 32)
BAR = (38, 40, 48)
FG = (205, 208, 214)
DIM = (128, 132, 140)
GREEN = (126, 204, 120)
CYAN = (94, 197, 214)
YELLOW = (216, 198, 112)
MAGENTA = (198, 132, 208)
RED = (224, 120, 120)
BLUE = (120, 168, 228)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
SIZE = 15
LINE_H = 22
COLS_MARGIN = 16
TOP_BAR = 30
ROWS = 24            # visible rows (scrolling viewport)
WIDTH = 900

reg = ImageFont.truetype(os.path.join(FONT_DIR, "DejaVuSansMono.ttf"), SIZE)
try:
    bold = ImageFont.truetype(os.path.join(FONT_DIR, "DejaVuSansMono-Bold.ttf"), SIZE)
except OSError:
    bold = reg

CHAR_W = reg.getbbox("M")[2]
HEIGHT = TOP_BAR + ROWS * LINE_H + COLS_MARGIN


# --- transcript: list of (segments, hold_frames) -------------------------- #
# segments = list of (text, color, is_bold)
def L(*segments, hold=1):
    return (list(segments), hold)


def seg(text, color=FG, b=False):
    return (text, color, b)


TR = [
    L(seg("  gitmemory", MAGENTA, True), seg("  — git-native AI memory for GitHub", DIM)),
    L(seg("  backend: google/gemma-4-e4b via LM Studio  (100% local)", DIM)),
    L(),
    L(seg("━━ STEP 1 ", CYAN, True), seg("──────────────────────────────────────", DIM)),
    L(seg("   Ingest project history (PRs & issues)", FG, True)),
    L(),
    L(seg("  $ ", GREEN), seg("gitmemory ingest --source PR#12 --file PR-12.md", GREEN, True)),
    L(seg("    + ", GREEN), seg("[decision] ", YELLOW),
      seg("Orders use optimistic locking, not row locks.")),
    L(seg("  $ ", GREEN), seg("gitmemory ingest --source issue#33 --file issue-33.md", GREEN, True)),
    L(seg("    + ", GREEN), seg("[gotcha] ", YELLOW),
      seg("Inventory cache TTL must stay under 60 seconds.")),
    L(seg("  $ ", GREEN), seg("gitmemory ingest --source PR#25 --file PR-25.md", GREEN, True)),
    L(seg("    + ", GREEN), seg("[dead_end] ", YELLOW),
      seg("Server-side rendering for the dashboard is a dead end.")),
    L(seg("  → store: 8 active, 0 superseded, 0 retracted", DIM), hold=6),
    L(),
    L(seg("━━ STEP 2 ", CYAN, True), seg("──────────────────────────────────────", DIM)),
    L(seg("   A new PR opens — recall surfaces relevant memory", FG, True)),
    L(),
    L(seg("  $ ", GREEN), seg('echo "add row locks to fix order deadlocks" | gitmemory recall', GREEN, True)),
    L(seg("    Relevant project memory:", CYAN, True)),
    L(seg("    * ", MAGENTA), seg("Orders use optimistic locking, not row locks.  ", FG),
      seg("(PR#12)", DIM)),
    L(seg("      why: ", DIM), seg("row locks caused deadlocks under checkout load", DIM)),
    L(seg("      relevance: ", DIM), seg("0.80", GREEN, True), hold=8),
    L(),
    L(seg("━━ STEP 3 ", CYAN, True), seg("──────────────────────────────────────", DIM)),
    L(seg("   The PR reverses it — reconcile SUPERSEDES the stale memory", FG, True)),
    L(),
    L(seg("  $ ", GREEN), seg("gitmemory ingest --source PR#41  ", GREEN, True),
      seg("# row locks replace optimistic locking", DIM)),
    L(seg("    + ", GREEN), seg("[decision] ", YELLOW),
      seg("Orders now use SELECT FOR UPDATE row-level locking.")),
    L(seg("    ~ SUPERSEDE ", MAGENTA, True), seg("mem_50ed60", DIM),
      seg(" → ", DIM), seg("mem_f953d9", DIM)),
    L(seg("      row-level locking replaces the optimistic-locking decision", DIM), hold=8),
    L(),
    L(seg("━━ STEP 4 ", CYAN, True), seg("──────────────────────────────────────", DIM)),
    L(seg("   Recall again — the stale decision is gone", FG, True)),
    L(),
    L(seg("  $ ", GREEN), seg('echo "order locking approach?" | gitmemory recall', GREEN, True)),
    L(seg("    Relevant project memory:", CYAN, True)),
    L(seg("    * ", MAGENTA), seg("Orders now use row-level locking.  ", FG), seg("(PR#41)", DIM),
      seg("  0.79", GREEN, True)),
    L(seg("    x ", RED), seg("Optimistic locking is no longer used.  ", FG), seg("(PR#41)", DIM)),
    L(seg("      ", DIM), seg("(the superseded PR#12 decision no longer surfaces)", DIM)),
    L(),
    L(seg("  ✔ done. ", GREEN, True),
      seg("stale memory is never recalled.", DIM), hold=30),
]


def render_frame(visible_lines):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    # title bar with traffic-light dots
    d.rectangle([0, 0, WIDTH, TOP_BAR], fill=BAR)
    for i, col in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
        d.ellipse([16 + i * 22, 10, 28 + i * 22, 22], fill=col)
    d.text((WIDTH // 2 - 70, 7), "gitmemory — demo", font=reg, fill=DIM)

    y = TOP_BAR + 6
    for segments in visible_lines:
        x = COLS_MARGIN
        for text, color, b in segments:
            d.text((x, y), text, font=(bold if b else reg), fill=color)
            x += len(text) * CHAR_W
        y += LINE_H
    return img


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "demo.gif")

    frames, durations = [], []
    revealed = []
    for segments, hold in TR:
        revealed.append(segments)
        window = revealed[-ROWS:]
        frames.append(render_frame(window))
        durations.append(140 + hold * 90)   # ms; longer pause on `hold`

    # a final hold on the last frame
    frames.append(frames[-1])
    durations.append(2500)

    frames[0].save(
        out, save_all=True, append_images=frames[1:],
        duration=durations, loop=0, optimize=True,
    )
    size_kb = os.path.getsize(out) / 1024
    print(f"Wrote {out}  ({len(frames)} frames, {size_kb:.0f} KB)")


if __name__ == "__main__":
    main()

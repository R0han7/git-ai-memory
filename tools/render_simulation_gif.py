"""Render an animated GIF of the gitmemory usability simulation.

Same terminal style as tools/render_demo_gif.py. The transcript is the real
output captured from `simulation/simulate.py` on google/gemma-4-e4b, trimmed to
fit a terminal frame. Produces docs/simulation.gif.

Run:  python tools/render_simulation_gif.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

BG = (24, 26, 32)
BAR = (38, 40, 48)
FG = (205, 208, 214)
DIM = (128, 132, 140)
GREEN = (126, 204, 120)
CYAN = (94, 197, 214)
YELLOW = (216, 198, 112)
MAGENTA = (198, 132, 208)
RED = (224, 120, 120)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
SIZE = 15
LINE_H = 22
MARGIN = 16
TOP_BAR = 30
ROWS = 22
WIDTH = 940

reg = ImageFont.truetype(os.path.join(FONT_DIR, "DejaVuSansMono.ttf"), SIZE)
try:
    bold = ImageFont.truetype(os.path.join(FONT_DIR, "DejaVuSansMono-Bold.ttf"), SIZE)
except OSError:
    bold = reg
CHAR_W = reg.getbbox("M")[2]
HEIGHT = TOP_BAR + ROWS * LINE_H + MARGIN


def L(*segments, hold=1):
    return (list(segments), hold)


def s(text, color=FG, b=False):
    return (text, color, b)


TR = [
    L(s("  gitmemory simulation", MAGENTA, True),
      s("   real git repo · gemma-4-e4b (local)", DIM)),
    L(),
    L(s("━━ SCENE 1 · ", CYAN, True), s("Ingest history — three PRs merge", FG, True)),
    L(s("  $ ", GREEN), s("merge PR#101: adopt optimistic locking for orders", GREEN, True)),
    L(s("    + ", GREEN), s("[decision] ", YELLOW), s("optimistic locking chosen over row locks")),
    L(s("  $ ", GREEN), s("merge PR#102: cap inventory cache TTL", GREEN, True)),
    L(s("    + ", GREEN), s("[gotcha] ", YELLOW), s("inventory cache TTL must stay under 60s")),
    L(s("  $ ", GREEN), s("merge PR#103: store timestamps in UTC", GREEN, True)),
    L(s("    + ", GREEN), s("[convention] ", YELLOW), s("all timestamps stored in UTC")),
    L(s("  → committed to git · 3 active memories", DIM), hold=6),
    L(),
    L(s("━━ SCENE 2 · ", CYAN, True), s("A new PR opens — recall warns", FG, True)),
    L(s("  $ ", GREEN), s("open PR#104: add SELECT FOR UPDATE row locks to orders", GREEN, True)),
    L(s("    Relevant project memory:", CYAN, True)),
    L(s("    ! ", MAGENTA), s("optimistic locking chosen over row locks  ", FG), s("(PR#101)", DIM),
      s("  0.79", GREEN, True)),
    L(s("      why: ", DIM), s("row locks caused deadlocks under checkout load", DIM), hold=8),
    L(),
    L(s("━━ SCENE 3 · ", CYAN, True), s("The PR reverses it — SUPERSEDE", FG, True)),
    L(s("  $ ", GREEN), s("merge PR#104: switch orders to row-level locking", GREEN, True)),
    L(s("    + ", GREEN), s("[decision] ", YELLOW), s("orders now use SELECT FOR UPDATE row locks")),
    L(s("    ~ SUPERSEDE ", MAGENTA, True), s("mem_945e79 → mem_85dd2d", DIM)),
    L(s("      row locks replace the optimistic-locking decision", DIM), hold=8),
    L(),
    L(s("━━ SCENE 4 · ", CYAN, True), s("Recall again — stale memory is gone", FG, True)),
    L(s("  $ ", GREEN), s("open PR#105: what is our order-locking approach?", GREEN, True)),
    L(s("    * ", MAGENTA), s("orders use SELECT FOR UPDATE row locks  ", FG), s("(PR#104)", DIM),
      s("  0.81", GREEN, True)),
    L(s("    x ", RED), s("optimistic version-column locking abandoned  ", FG), s("(PR#104)", DIM),
      s("  0.82", GREEN, True)),
    L(s("      (the superseded PR#101 decision no longer surfaces)", DIM), hold=8),
    L(),
    L(s("━━ SCENE 5 · ", CYAN, True), s("Two branches edit memory — real git merge", FG, True)),
    L(s("  merge feature-api + feature-webhooks into main …", FG)),
    L(s("    ✔ both merges resolved with NO conflict ", GREEN, True),
      s("(union merge driver)", DIM), hold=8),
    L(),
    L(s("━━ SCENE 6 · ", CYAN, True), s("Memory is auditable git history", FG, True)),
    L(s("    444b200 ", YELLOW), s("Merge branch 'feature-webhooks'", DIM)),
    L(s("    75081ef ", YELLOW), s("PR#104: switch to row-level locking (reverses PR#101)", DIM)),
    L(s("    cf66600 ", YELLOW), s("PR#103: store timestamps in UTC", DIM)),
    L(s("    9c84bd6 ", YELLOW), s("PR#102: cap inventory cache TTL", DIM)),
    L(s("    f97b627 ", YELLOW), s("PR#101: adopt optimistic locking", DIM)),
    L(),
    L(s("  ✔ final: ", GREEN, True),
      s("7 active · 1 superseded — stale memory is never recalled", DIM), hold=30),
]


def render_frame(visible):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, TOP_BAR], fill=BAR)
    for i, c in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
        d.ellipse([16 + i * 22, 10, 28 + i * 22, 22], fill=c)
    d.text((WIDTH // 2 - 110, 7), "gitmemory — usability simulation", font=reg, fill=DIM)
    y = TOP_BAR + 6
    for segments in visible:
        x = MARGIN
        for text, color, b in segments:
            d.text((x, y), text, font=(bold if b else reg), fill=color)
            x += len(text) * CHAR_W
        y += LINE_H
    return img


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "simulation.gif")
    frames, durations, revealed = [], [], []
    for segments, hold in TR:
        revealed.append(segments)
        frames.append(render_frame(revealed[-ROWS:]))
        durations.append(150 + hold * 90)
    frames.append(frames[-1])
    durations.append(2500)
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)
    print(f"Wrote {out}  ({len(frames)} frames, {os.path.getsize(out)/1024:.0f} KB)")


if __name__ == "__main__":
    main()

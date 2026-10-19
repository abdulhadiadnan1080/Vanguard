#!/usr/bin/env python3
"""Generate the animated "how the loop works" SVGs for the README (light and dark).

    python3 docs/assets/make_loop_animation.py

Pure SVG + CSS animation (no scripts), so GitHub renders it inline as an image. One 16-second cycle, 8 stages of 2 s:
a glowing candidate travels round the loop while each station lights up, its spoke to the knowledge graph glows, and
a caption explains the step. The graph at the centre grows a node every cycle.
"""
import math
import os

W, H = 1200, 660
CX, CY, RX, RY = 600, 318, 452, 232
T = 16.0                      # seconds per cycle
STEP = T / 8                  # seconds per stage
DWELL = 1.35                  # seconds the candidate rests at a station before moving on
FADE = 0.25

STAGES = [
    ("Consult the graph", "what defeats each model?", "free",
     "The agent consults the knowledge graph: which weaknesses defeat which model, under what conditions, on how much evidence."),
    ("Rank advisories", "1,928 scored · ~12 s · $0", "free",
     "Every real security advisory is scored with each model's chance of failing, plus the reasons and the evidence behind it."),
    ("Narrow down", "1,928 → 1,590 → top 3", "free",
     "Unbuildable, duplicate and already-built candidates drop out. The strongest few get a complete build package."),
    ("Build the task", "AI agent · budgeted", "paid",
     "An AI agent triages the fix and builds a Docker task: the vulnerable code, an impact-only report, hidden tests."),
    ("You approve", "gate 1", "gate",
     "A person reviews the built task. Nothing that costs money or can't be undone happens without approval."),
    ("Validate", "fix ✓✓ · none ✗ · partial ½", "free",
     "Fairness checks in Docker: the real fix scores 1.0 twice, no fix scores 0, a partial fix lands in between."),
    ("Run the models", "Model A ✗ · Model B ✗", "paid",
     "Model A and Model B attempt the task. A solve ends it at once; a double failure earns more attempts at gate 2."),
    ("Learn", "forecast scored · retrain", "free",
     "Results flow back into the graph: the forecast is scored, the predictor retrains, and the next ranking is sharper."),
]

THEMES = {
    "light": dict(bg="#ffffff", card="#f6f8fa", line="#d0d7de", fg="#1f2328", muted="#59636e", accent="#2f46c9",
                  glow="#f0a020", spoke="#c8d0da", st_bg="#ffffff", st_on="#eef1ff", paid="#8250df", gate="#bf8700",
                  free="#1a7f37", n=["#c2334a", "#b06f12", "#2f6fb5", "#7048b8", "#1f8159", "#3f8593", "#7d6a50"]),
    "dark": dict(bg="#0d1117", card="#151b23", line="#30363d", fg="#e6edf3", muted="#9198a1", accent="#8d9cff",
                 glow="#ffc35a", spoke="#2a3341", st_bg="#0d1117", st_on="#1b2242", paid="#ab7df8", gate="#d29922",
                 free="#3fb950", n=["#f06a7e", "#e3a54a", "#68a6ea", "#ab88ef", "#4cc292", "#78c0cc", "#bca98d"]),
}


def station_xy(i):
    a = math.radians(-90 + 45 * i)
    return CX + RX * math.cos(a), CY + RY * math.sin(a)


def arc_fractions():
    """Fraction of the ellipse's length (clockwise from the top) at which each station sits."""
    pts = [(CX + RX * math.cos(math.radians(-90 + 360 * k / 3600)), CY + RY * math.sin(math.radians(-90 + 360 * k / 3600)))
           for k in range(3601)]
    cum = [0.0]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
    return [cum[i * 450] / cum[-1] for i in range(8)]


def windows_css(name, windows, on, off):
    """@keyframes that hold `on` during each (start, end) window (seconds in the cycle) and `off` elsewhere."""
    stops = {0.0: off, 100.0: off}
    for s, e in windows:
        p = lambda t: max(0.0, min(100.0, 100 * t / T))
        stops[p(s)] = off
        stops[p(s + FADE)] = on
        stops[p(e - FADE)] = on
        stops[p(e)] = off
    body = " ".join("%.3f%%{%s}" % (k, v) for k, v in sorted(stops.items()))
    return "@keyframes %s{%s}" % (name, body)


def hub_nodes():
    """A small deterministic 'knowledge graph' for the centre."""
    nodes = [(0, 0, 11, 0)]
    for i in range(16):
        ang = i * 2.399963  # golden angle: an even, organic spread
        r = 30 + 78 * math.sqrt((i + 1) / 16)
        nodes.append((r * math.cos(ang), r * math.sin(ang) * 0.9, 5 + (i % 3), 1 + i % 6))
    edges = [(0, k) for k in (1, 2, 3, 5, 8)] + [(i, i + 1) for i in range(1, 15, 2)] + \
            [(i, i + 5) for i in range(1, 11, 3)] + [(2, 9), (4, 12), (6, 16), (3, 14)]
    return nodes, edges


def build(theme):
    c = THEMES[theme]
    fr = arc_fractions()
    css = []
    # the travelling candidate: dwell at each station, then move along the ellipse to the next
    stops = []
    for i in range(8):
        t0, t1 = i * STEP, i * STEP + DWELL
        d0 = fr[i] * 100
        stops += ["%.3f%%{offset-distance:%.3f%%}" % (100 * t0 / T, d0), "%.3f%%{offset-distance:%.3f%%}" % (100 * t1 / T, d0)]
    stops.append("100%{offset-distance:100%}")
    css.append("@keyframes travel{%s}" % " ".join(stops))
    ellipse_path = "M %d %d A %d %d 0 1 1 %d %d A %d %d 0 1 1 %d %d" % (CX, CY - RY, RX, RY, CX, CY + RY, RX, RY, CX, CY - RY)
    css.append(".cand{offset-path:path('%s');offset-rotate:0deg;animation:travel %gs linear infinite}" % (ellipse_path, T))
    for i in range(8):
        w = [(i * STEP, (i + 1) * STEP)]
        css.append(windows_css("st%d" % i, w, "opacity:1", "opacity:0"))
        css.append(windows_css("sp%d" % i, w, "opacity:1", "opacity:0.0"))
        css.append(windows_css("cap%d" % i, w, "opacity:1", "opacity:0"))
        # one `animation` rule per element: a second class setting `animation` would silently override this one
        flow = "inflow" if i == 7 else "flow"  # "Learn" flows back into the graph; every other stage reads from it
        css.append(".on%d{animation:st%d %gs linear infinite}"
                   ".spk%d{stroke-dasharray:6 6;animation:sp%d %gs linear infinite,%s .8s linear infinite}"
                   ".cap%d{animation:cap%d %gs linear infinite}" % (i, i, T, i, i, T, flow, i, i, T))
    css.append(windows_css("hub", [(0, STEP), (7 * STEP, 8 * STEP)], "opacity:1", "opacity:0"))
    css.append(".hubglow{animation:hub %gs linear infinite}" % T)
    # the graph learns: a new node and its edge appear during "Learn" and stay until the cycle restarts
    css.append("@keyframes grow{0%%,%.2f%%{opacity:0;transform:scale(.2)}%.2f%%,97%%{opacity:1;transform:scale(1)}100%%{opacity:0}}"
               % (100 * (7 * STEP + 0.6) / T, 100 * (7 * STEP + 1.1) / T))
    css.append(".newnode{transform-box:fill-box;transform-origin:center;animation:grow %gs ease-out infinite}" % T)
    css.append("@keyframes flow{to{stroke-dashoffset:-24}}@keyframes inflow{to{stroke-dashoffset:24}}")
    css.append("@keyframes breathe{0%,100%{opacity:.55}50%{opacity:1}}.breathe{animation:breathe 3s ease-in-out infinite}")
    css.append("@media (prefers-reduced-motion:reduce){*{animation:none!important}.cand{offset-distance:0%}"
               ".on0,.spk0,.cap0{opacity:1}}")
    css.append("text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}"
               ".mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}")

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
           'aria-label="How Vanguard works: consult the knowledge graph, rank advisories, narrow down, build the task, '
           'approve, validate, run the models, learn, and repeat">' % (W, H, W, H),
           "<title>How Vanguard works</title>",
           "<defs><filter id='glow' x='-80%%' y='-80%%' width='260%%' height='260%%'><feGaussianBlur stdDeviation='6' result='b'/>"
           "<feMerge><feMergeNode in='b'/><feMergeNode in='SourceGraphic'/></feMerge></filter>"
           "<radialGradient id='halo'><stop offset='0' stop-color='%s' stop-opacity='.35'/>"
           "<stop offset='1' stop-color='%s' stop-opacity='0'/></radialGradient></defs>" % (c["accent"], c["accent"]),
           "<style>%s</style>" % "".join(css),
           '<rect x="1" y="1" width="%d" height="%d" rx="18" fill="%s" stroke="%s"/>' % (W - 2, H - 2, c["card"], c["line"]),
           '<text x="34" y="46" font-size="13" letter-spacing="2.5" fill="%s" class="mono">THE VANGUARD LOOP</text>' % c["muted"],
           '<text x="34" y="70" font-size="13" fill="%s">rank → build → validate → run → learn → repeat</text>' % c["muted"]]

    # the loop track
    out.append('<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="none" stroke="%s" stroke-width="2" stroke-dasharray="2 7"/>'
               % (CX, CY, RX, RY, c["line"]))
    # spokes: every stage reads from or writes to the graph
    for i in range(8):
        x, y = station_xy(i)
        dx, dy = x - CX, y - CY
        # spokes start just outside the graph; the one straight down starts below the graph's label instead
        k = (172 if abs(dx) < 1 and dy > 0 else 122) / math.hypot(dx, dy)
        x0, y0 = CX + dx * k, CY + dy * k
        k2 = 1 - 44 / math.hypot(dx, dy)
        x1, y1 = CX + dx * k2, CY + dy * k2
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1.2"/>' % (x0, y0, x1, y1, c["spoke"]))
        out.append('<line class="spk%d" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2.6" opacity="0"/>'
                   % (i, x0, y0, x1, y1, c["accent"]))

    # hub: the knowledge graph
    nodes, edges = hub_nodes()
    out.append('<circle class="hubglow" cx="%d" cy="%d" r="130" fill="url(#halo)" opacity="0"/>' % (CX, CY))
    out.append('<g class="breathe">')
    for a, b in edges:
        (xa, ya, _, _), (xb, yb, _, _) = nodes[a], nodes[b]
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>'
                   % (CX + xa, CY + ya, CX + xb, CY + yb, c["muted"]))
    out.append("</g>")
    for x, y, r, t in nodes:
        out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"/>' % (CX + x, CY + y, r, c["fg"] if t == 0 else c["n"][t - 1]))
    nx, ny = CX + 96, CY - 52
    out.append('<g class="newnode"><line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1.6"/>'
               '<circle cx="%.1f" cy="%.1f" r="6.5" fill="%s" stroke="%s" stroke-width="2"/></g>'
               % (CX + nodes[5][0], CY + nodes[5][1], nx, ny, c["glow"], nx, ny, c["n"][2], c["glow"]))
    out.append('<text x="%d" y="%d" text-anchor="middle" font-size="15" font-weight="700" fill="%s">Knowledge graph</text>'
               % (CX, CY + 138, c["fg"]))
    out.append('<text x="%d" y="%d" text-anchor="middle" font-size="12" fill="%s" class="mono">weaknesses · evidence · outcomes</text>'
               % (CX, CY + 157, c["muted"]))

    # the candidate travelling round the loop (drawn under the stations: it slips behind one while it works there)
    out.append('<g class="cand"><circle r="15" fill="%s" opacity=".25" filter="url(#glow)"/>'
               '<circle r="8" fill="%s" filter="url(#glow)"/><circle r="3.5" fill="#fff"/></g>' % (c["glow"], c["glow"]))

    # stations
    tag_color = {"free": c["free"], "paid": c["paid"], "gate": c["gate"]}
    tag_text = {"free": "free", "paid": "paid", "gate": "you decide"}
    for i, (title, sub, kind, _) in enumerate(STAGES):
        x, y = station_xy(i)
        w, h = 252, 66
        x0, y0 = x - w / 2, y - h / 2
        border = c["gate"] if kind == "gate" else c["line"]
        out.append('<rect x="%.1f" y="%.1f" width="%d" height="%d" rx="12" fill="%s" stroke="%s" stroke-width="1.4"/>'
                   % (x0, y0, w, h, c["st_bg"], border))
        out.append('<rect class="on%d" x="%.1f" y="%.1f" width="%d" height="%d" rx="12" fill="%s" stroke="%s" stroke-width="2.4" opacity="0"/>'
                   % (i, x0, y0, w, h, c["st_on"], c["accent"]))
        out.append('<circle cx="%.1f" cy="%.1f" r="13" fill="%s"/>' % (x0 + 24, y, c["accent"]))
        out.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="13" font-weight="700" fill="%s">%d</text>'
                   % (x0 + 24, y + 4.5, c["card"], i + 1))
        out.append('<text x="%.1f" y="%.1f" font-size="15" font-weight="700" fill="%s">%s</text>' % (x0 + 46, y - 4, c["fg"], title))
        out.append('<text x="%.1f" y="%.1f" font-size="11.5" fill="%s" class="mono">%s</text>' % (x0 + 46, y + 15, c["muted"], sub))
        out.append('<text x="%.1f" y="%.1f" text-anchor="end" font-size="10" font-weight="700" fill="%s" class="mono">%s</text>'
                   % (x0 + w - 10, y0 + 14, tag_color[kind], tag_text[kind].upper()))

    # captions
    for i, (title, _, _, cap) in enumerate(STAGES):
        out.append('<g class="cap%d" opacity="0"><text x="%d" y="%d" text-anchor="middle" font-size="16" fill="%s">'
                   '<tspan font-weight="700" fill="%s">%d · %s.</tspan> %s</text></g>'
                   % (i, W // 2, H - 22, c["fg"], c["accent"], i + 1, title, cap))
    out.append("</svg>")
    return "\n".join(out)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for theme in THEMES:
        path = os.path.join(here, "loop-animation-%s.svg" % theme)
        with open(path, "w", encoding="utf-8") as f:
            f.write(build(theme))
        print("wrote", os.path.relpath(path), os.path.getsize(path), "bytes")

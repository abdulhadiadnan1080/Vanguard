"""Builds docs/index.html, the animated tour, from tour_template.html.

The full knowledge graph (docs/graph.html) is embedded in an iframe `srcdoc`, so its styles and scripts stay isolated
from the tour; it is forced to the dark theme and its own header and node index are hidden. The loop animation SVG is
inlined. The result is one self-contained file: the same page can be served by GitHub Pages or sent as a single file.

    python3 docs/assets/make_tour.py [--copy PATH]
"""
import argparse
import html
import os
import shutil

ASSETS = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(ASSETS)

EMBED_STYLE = ("<style>header,section.index{display:none!important}"
               "body{padding-block:0!important;padding-inline:0!important;background:#0e131a}"
               ".wrap{max-width:none!important;gap:10px!important;padding:10px}"
               "@media (min-width:900px){.canvas,.panel{height:calc(100vh - 130px)!important;max-height:none!important}}</style>")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def graph_srcdoc():
    page = read(os.path.join(DOCS, "graph.html"))
    page = page.replace('<html lang="en">', '<html lang="en" data-theme="dark">', 1)
    page = page.replace("</head>", EMBED_STYLE + "</head>", 1)
    return html.escape(page, quote=True)


def loop_svg():
    svg = read(os.path.join(ASSETS, "loop-animation-dark.svg"))
    return svg[svg.index("<svg"):]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--copy", help="also write a copy of the tour here")
    args = ap.parse_args()
    page = read(os.path.join(ASSETS, "tour_template.html"))
    page = page.replace("{{GRAPH_SRCDOC}}", graph_srcdoc()).replace("{{LOOP_SVG}}", loop_svg())
    assert "{{" not in page, "unfilled placeholder"
    out = os.path.join(DOCS, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote", out, "(%d KB)" % (len(page.encode()) // 1024))
    if args.copy:
        shutil.copyfile(out, args.copy)
        print("wrote", args.copy)


if __name__ == "__main__":
    main()

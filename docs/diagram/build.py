"""Build the propbot architecture diagram from one layout spec.

Writes two files:
  docs/architecture.svg     self contained picture for the README (logos embedded, no web requests)
  docs/architecture.drawio  the same diagram as editable draw.io XML

Brand logos come from simple-icons (CC0), the other icons from Lucide (ISC); both licences are
in docs/diagram/icons. Run:  python docs/diagram/build.py
"""
from __future__ import annotations

import base64
import re
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
OUT_SVG = HERE.parent / "architecture.svg"
OUT_DRAWIO = HERE.parent / "architecture.drawio"

W, H = 1600, 1180
FONT = "-apple-system, 'Segoe UI', Inter, Roboto, Helvetica, Arial, sans-serif"

# brand logos: (file, colour)
BRAND = {"telegram": "#26A5E4", "docker": "#2496ED", "claude": "#D97757", "obsidian": "#7C3AED",
         "sqlite": "#003B57", "python": "#3776AB"}


def icon_svg(name: str, colour: str) -> str:
    """Return a standalone SVG string for an icon, coloured."""
    if name in BRAND:
        raw = (ICONS / f"{name}.svg").read_text()
        raw = re.sub(r"<title>.*?</title>", "", raw)
        return raw.replace("<svg ", f'<svg fill="{colour}" ', 1)
    raw = (ICONS / f"lucide_{name}.svg").read_text()
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S).strip()
    raw = re.sub(r'\s+class="[^"]*"', "", raw)
    return raw.replace('stroke="currentColor"', f'stroke="{colour}"')


def icon_inner(name: str, colour: str) -> tuple[str, str]:
    """(attributes for a <g>, inner markup) so the icon can be drawn inline at any size."""
    svg = icon_svg(name, colour)
    inner = re.sub(r"^.*?<svg[^>]*>", "", svg, flags=re.S)
    inner = re.sub(r"</svg>\s*$", "", inner.strip())
    if name in BRAND:
        return f'fill="{colour}"', inner
    return (f'fill="none" stroke="{colour}" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round"'), inner


# draw.io's own icon service, used for the lightweight connector version (model_web.xml)
WEB_ICONS = {
    "telegram": "https://icons.diagrams.net/icon-cache1/Brands_Pack-2317/telegram-1286.svg",
    "docker": "https://app.diagrams.net/img/lib/mscae/Docker.svg",
    "claude": "https://icons.diagrams.net/assets/font-awesome/1/Claude_brand.svg",
    "obsidian": "https://icons.diagrams.net/assets/font-awesome/1/Obsidian_brand.svg",
    "python": "https://icons.diagrams.net/assets/font-awesome/1/Python_brand.svg",
    "sqlite": "https://icons.diagrams.net/assets/databases/1/SQL-36.svg",
}
USE_WEB_ICONS = False


def data_uri(name: str, colour: str) -> str:
    if USE_WEB_ICONS and name in WEB_ICONS:
        return WEB_ICONS[name]
    return "data:image/svg+xml," + base64.b64encode(icon_svg(name, colour).encode()).decode()


# ------------------------------------------------------------ layout spec
# card: id, x, y, w, h, icon, icon colour, title, lines, accent colour
CARDS = [
    ("official", 60, 205, 250, 110, "landmark", "#B91C1C", "Official data",
     ["data.gov.sg, URA, MAS,", "HDB, CPF and OneMap"], "#B91C1C"),
    ("listings", 60, 337, 250, 110, "globe", "#0F766E", "Listing sites and news",
     ["Polite fetcher: robots.txt,", "slow gaps, daily budgets"], "#0F766E"),
    ("rules", 60, 469, 250, 110, "scale", "#B45309", "Rules file",
     ["Every legal figure with its", "official source, checked weekly"], "#B45309"),
    ("profile", 60, 601, 250, 110, "obsidian", BRAND["obsidian"], "Your vault notes",
     ["Profile.md and Watchlist.md", "read at the start of a run"], BRAND["obsidian"]),
    ("discovery", 350, 330, 220, 124, "claude", BRAND["claude"], "Claude discovery",
     ["Web search finds listings", "and launches, 2 calls a day"], BRAND["claude"]),
    ("curation", 985, 290, 255, 124, "claude", BRAND["claude"], "Claude writes the words",
     ["Explains the verdict.", "It cannot change a number."], BRAND["claude"]),
    ("render", 985, 470, 255, 110, "message-square-text", "#0369A1", "Renderer",
     ["One item per message,", "under 4,096 characters"], "#0369A1"),
    ("sqlite", 640, 800, 280, 104, "sqlite", BRAND["sqlite"], "SQLite database",
     ["Every calculation with the", "exact rule values used"], BRAND["sqlite"]),
    ("vault", 985, 800, 255, 104, "obsidian", BRAND["obsidian"], "Obsidian vault",
     ["Cards, daily index and an", "activity log of every action"], BRAND["obsidian"]),
    ("telegram", 1320, 290, 250, 124, "telegram", BRAND["telegram"], "Telegram channel",
     ["About 100 items at 08:30,", "yesterday's posts deleted"], BRAND["telegram"]),
    ("admin", 1320, 470, 250, 110, "bell", "#DC2626", "Admin chat",
     ["Alerts: cooldowns, fallbacks,", "rule changes"], "#DC2626"),
    ("private", 1320, 640, 250, 110, "user-round", "#4338CA", "Your private chat",
     ["/analyse, /set, forward a", "post to keep a favourite"], "#4338CA"),
    # website lane: how the family website is fed and served
    ("pages", 60, 990, 215, 110, "globe", "#0F766E", "Listing pages",
     ["PropNex hourly, EdgeProp", "photos, OneMap maps"], "#0F766E"),
    ("builder", 300, 990, 215, 110, "calculator", "#4F46E5", "Site builder",
     ["Hourly: verdict, outlook,", "money, nearby, feng shui"], "#4F46E5"),
    ("datafile", 540, 990, 215, 110, "file-text", "#B45309", "One data file",
     ["Rewritten in one step so", "readers never see half"], "#B45309"),
    ("web", 780, 990, 215, 110, "docker", BRAND["docker"], "Web container",
     ["nginx serves the page", "and the data file"], BRAND["docker"]),
    ("tunnel", 1020, 990, 215, 110, "shield-check", "#0369A1", "Public tunnel",
     ["Own network node, HTTPS,", "no router ports opened"], "#0369A1"),
    ("family", 1320, 990, 250, 110, "user-round", "#4338CA", "Family and friends",
     ["Open the link in any", "browser, no login"], "#4338CA"),
]

ENGINE = (610, 190, 340, 570)
STEPS = [
    ("Eligibility", "What the law lets you buy"),
    ("Costs and duties", "BSD, ABSD, SSD, fees, taxes"),
    ("Financing", "LTV, TDSR, MSR, CPF, instalment"),
    ("10 year projection", "Bear, base, bull, IRR, break even"),
    ("Verdict", "Score 0 to 5, fixed by rules"),
]
STEP_Y0, STEP_H, STEP_GAP = 282, 72, 18
NAS = (30, 100, 1250, 1040)
SCHED = (350, 118, 590, 44)

# edges: points, colour, label, label position (index of segment), dashed
EDGES = [
    ([(310, 260), (610, 260)], "#B91C1C", "official figures", False, (440, 260)),
    ([(310, 392), (350, 392)], "#0F766E", "", False),
    ([(570, 392), (610, 392)], BRAND["claude"], "", False),
    ([(310, 524), (610, 524)], "#B45309", "legal figures", False),
    ([(310, 656), (610, 656)], BRAND["obsidian"], "your profile", False),
    ([(950, 352), (985, 352)], "#4F46E5", "", False),
    ([(1112, 414), (1112, 470)], BRAND["claude"], "words", False),
    ([(1240, 510), (1280, 510), (1280, 352), (1320, 352)], BRAND["telegram"], "", False),
    ([(1240, 540), (1320, 540)], "#DC2626", "", False),
    ([(1320, 695), (1290, 695), (1290, 776), (880, 776), (880, 760)], "#4338CA", "commands", False, (1010, 776)),
    ([(700, 760), (700, 800)], BRAND["sqlite"], "", False),
    ([(1200, 580), (1200, 800)], BRAND["obsidian"], "notes and log", False, (1200, 690)),
    ([(548, 162), (548, 330)], "#64748B", "", True),
    ([(660, 904), (660, 950), (407, 950), (407, 990)], "#4F46E5", "tracked listings", False, (530, 950)),
    ([(275, 1045), (300, 1045)], "#0F766E", "", False),
    ([(515, 1045), (540, 1045)], "#4F46E5", "", False),
    ([(755, 1045), (780, 1045)], "#B45309", "", False),
    ([(995, 1045), (1020, 1045)], BRAND["docker"], "", False),
    ([(1235, 1045), (1320, 1045)], "#0369A1", "public link", False, (1278, 1045)),
]


# ------------------------------------------------------------ SVG
def svg_icon(name, colour, x, y, size):
    attrs, inner = icon_inner(name, colour)
    s = size / 24
    return f'<g transform="translate({x},{y}) scale({s})" {attrs}>{inner}</g>'


def svg_text(x, y, text, size=14, weight=400, colour="#0F172A", anchor="start"):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{colour}" text-anchor="{anchor}">{escape(text)}</text>')


def build_svg() -> str:
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
         "<defs>",
         '<filter id="sh" x="-10%" y="-10%" width="120%" height="140%">'
         '<feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0F172A" flood-opacity="0.10"/></filter>',
         '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F8FAFC"/>'
         '<stop offset="1" stop-color="#EEF2F7"/></linearGradient>',
         '<linearGradient id="eng" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#EEF2FF"/>'
         '<stop offset="1" stop-color="#E0E7FF"/></linearGradient>']
    for colour in sorted({e[1] for e in EDGES} | {"#4F46E5"}):
        cid = colour.strip("#")
        o.append(f'<marker id="a{cid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                 f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{colour}"/></marker>')
    o.append("</defs>")
    o.append(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
    # title
    o.append(svg_text(40, 50, "propbot", 30, 800, "#0F172A"))
    o.append(svg_text(178, 50, "how a day runs", 30, 300, "#475569"))
    o.append(svg_text(40, 78, "From official data to one Telegram message per item. "
                      "All money maths is code; Claude only finds listings and writes the words.", 15, 400, "#64748B"))
    # NAS container
    x, y, w, h = NAS
    o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="22" fill="#FFFFFF" fill-opacity="0.55" '
             f'stroke="#94A3B8" stroke-width="1.5" stroke-dasharray="7 6"/>')
    o.append(svg_icon("docker", BRAND["docker"], x + 22, y + 14, 26))
    o.append(svg_text(x + 58, y + 34, "UGREEN NAS · Docker container", 15, 700, "#334155"))
    # scheduler pill
    sx, sy, sw, sh = SCHED
    o.append(f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" rx="22" fill="#0F172A" filter="url(#sh)"/>')
    o.append(svg_icon("clock", "#FDE68A", sx + 16, sy + 11, 22))
    o.append(svg_text(sx + 48, sy + 28, "Scheduler   06:00 prepare  ·  08:30 post  ·  Monday 05:00 rules check",
                      14, 600, "#F8FAFC"))
    # column headers
    o.append(svg_text(62, 192, "INPUTS", 12, 700, "#94A3B8"))
    o.append(svg_text(1322, 270, "WHAT YOU SEE", 12, 700, "#94A3B8"))
    o.append(svg_text(642, 790, "", 12, 700, "#94A3B8"))
    # engine
    ex, ey, ew, eh = ENGINE
    o.append(f'<rect x="{ex}" y="{ey}" width="{ew}" height="{eh}" rx="20" fill="url(#eng)" stroke="#6366F1" '
             f'stroke-width="2" filter="url(#sh)"/>')
    o.append(f'<rect x="{ex + 18}" y="{ey + 16}" width="44" height="44" rx="12" fill="#FFFFFF"/>')
    o.append(svg_icon("python", BRAND["python"], ex + 28, ey + 26, 24))
    o.append(svg_text(ex + 76, ey + 37, "Finance engine", 19, 800, "#312E81"))
    o.append(svg_text(ex + 76, ey + 57, "Code only. No AI does the maths.", 13, 500, "#4338CA"))
    for i, (title, sub) in enumerate(STEPS):
        sy_ = STEP_Y0 + i * (STEP_H + STEP_GAP)
        o.append(f'<rect x="{ex + 22}" y="{sy_}" width="{ew - 44}" height="{STEP_H}" rx="14" fill="#FFFFFF" '
                 f'stroke="#C7D2FE" stroke-width="1.2"/>')
        o.append(f'<circle cx="{ex + 52}" cy="{sy_ + STEP_H / 2}" r="15" fill="#4F46E5"/>')
        o.append(svg_text(ex + 52, sy_ + STEP_H / 2 + 5, str(i + 1), 14, 800, "#FFFFFF", "middle"))
        o.append(svg_text(ex + 80, sy_ + 31, title, 15, 700, "#1E1B4B"))
        o.append(svg_text(ex + 80, sy_ + 52, sub, 13, 400, "#475569"))
        if i < len(STEPS) - 1:
            ay = sy_ + STEP_H
            o.append(f'<path d="M{ex + 52},{ay} L{ex + 52},{ay + STEP_GAP}" stroke="#818CF8" stroke-width="2" '
                     f'marker-end="url(#a4F46E5)"/>')
    # edges under cards
    for pts, colour, label, dashed, *_ in EDGES:
        d = "M" + " L".join(f"{px},{py}" for px, py in pts)
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        o.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2.4" stroke-linejoin="round"'
                 f'{dash} marker-end="url(#a{colour.strip("#")})"/>')
    # cards
    for cid, x, y, w, h, icon, icol, title, lines, accent in CARDS:
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="#FFFFFF" stroke="#E2E8F0" '
                 f'stroke-width="1.2" filter="url(#sh)"/>')
        o.append(f'<rect x="{x}" y="{y + 16}" width="5" height="{h - 32}" rx="2.5" fill="{accent}"/>')
        o.append(f'<rect x="{x + 18}" y="{y + 16}" width="42" height="42" rx="11" fill="{accent}" fill-opacity="0.10"/>')
        o.append(svg_icon(icon, icol, x + 27, y + 25, 24))
        o.append(svg_text(x + 72, y + 34, title, 15, 700, "#0F172A"))
        for j, line in enumerate(lines):
            o.append(svg_text(x + 18, y + 80 + j * 18, line, 13, 400, "#475569"))
    # edge labels on top
    for pts, colour, label, dashed, *pos in EDGES:
        if not label:
            continue
        (x1, y1), (x2, y2) = pts[0], pts[1]
        mx, my = pos[0] if pos else ((x1 + x2) / 2, (y1 + y2) / 2)
        tw = 7.2 * len(label) + 16
        o.append(f'<rect x="{mx - tw / 2}" y="{my - 11}" width="{tw}" height="22" rx="11" fill="#FFFFFF" '
                 f'stroke="{colour}" stroke-width="1"/>')
        o.append(svg_text(mx, my + 4.5, label, 12, 600, colour, "middle"))
    o.append(svg_text(W - 40, H - 22, "Model estimates, not financial advice.  Logos: simple-icons (CC0) and Lucide (ISC).",
                      11, 400, "#94A3B8", "end"))
    o.append("</svg>")
    return "\n".join(o)


# ------------------------------------------------------------ draw.io XML
def build_drawio() -> str:
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    n = [1]

    def nid():
        n[0] += 1
        return f"c{n[0]}"

    def vertex(value, style, x, y, w, h, cid=None):
        cid = cid or nid()
        cells.append(f'<mxCell id="{cid}" value="{escape(value, quote=True)}" style="{escape(style, quote=True)}" '
                     f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def image(name, colour, x, y, s):
        return vertex("", f"shape=image;aspect=fixed;imageAspect=0;image={data_uri(name, colour)};", x, y, s, s)

    vertex('<b style="font-size:30px">propbot</b><span style="font-size:30px;color:#475569"> how a day runs</span>'
           '<br><span style="font-size:15px;color:#64748B">From official data to one Telegram message per item. '
           'All money maths is code; Claude only finds listings and writes the words.</span>',
           "text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;", 40, 18, 1200, 70)
    x, y, w, h = NAS
    vertex('<b>UGREEN NAS · Docker container</b>',
           "rounded=1;arcSize=3;html=1;fillColor=#FFFFFF;opacity=60;strokeColor=#94A3B8;dashed=1;dashPattern=7 6;"
           "align=left;verticalAlign=top;spacingLeft=56;spacingTop=10;fontSize=15;fontColor=#334155;", x, y, w, h)
    image("docker", BRAND["docker"], x + 22, y + 14, 26)
    sx, sy, sw, sh = SCHED
    vertex("<b>Scheduler</b>&nbsp;&nbsp; 06:00 prepare · 08:30 post · Monday 05:00 rules check",
           "rounded=1;arcSize=50;html=1;fillColor=#0F172A;strokeColor=none;fontColor=#F8FAFC;fontSize=14;"
           "align=left;spacingLeft=46;shadow=1;", sx, sy, sw, sh)
    image("clock", "#FDE68A", sx + 16, sy + 11, 22)
    vertex("INPUTS", "text;html=1;fontSize=12;fontStyle=1;fontColor=#94A3B8;align=left;", 62, 176, 120, 20)
    vertex("WHAT YOU SEE", "text;html=1;fontSize=12;fontStyle=1;fontColor=#94A3B8;align=left;", 1322, 254, 160, 20)
    ex, ey, ew, eh = ENGINE
    vertex('<b style="font-size:19px;color:#312E81">Finance engine</b><br>'
           '<span style="color:#4338CA">Code only. No AI does the maths.</span>',
           "rounded=1;arcSize=5;html=1;fillColor=#EEF2FF;gradientColor=#E0E7FF;strokeColor=#6366F1;strokeWidth=2;"
           "align=left;verticalAlign=top;spacingLeft=76;spacingTop=12;fontSize=13;shadow=1;", ex, ey, ew, eh,
           cid="engine")
    image("python", BRAND["python"], ex + 28, ey + 26, 24)
    step_ids = []
    for i, (title, sub) in enumerate(STEPS):
        sy_ = STEP_Y0 + i * (STEP_H + STEP_GAP)
        step_ids.append(vertex(f'<b style="font-size:15px;color:#1E1B4B">{escape(title)}</b><br>'
                               f'<span style="color:#475569">{escape(sub)}</span>',
                               "rounded=1;arcSize=20;html=1;fillColor=#FFFFFF;strokeColor=#C7D2FE;align=left;"
                               "spacingLeft=58;fontSize=13;", ex + 22, sy_, ew - 44, STEP_H))
        vertex(f"<b>{i + 1}</b>", "ellipse;html=1;fillColor=#4F46E5;strokeColor=none;fontColor=#FFFFFF;fontSize=14;",
               ex + 37, sy_ + STEP_H / 2 - 15, 30, 30)
    for a, b in zip(step_ids, step_ids[1:]):
        cells.append(f'<mxCell id="{nid()}" style="endArrow=block;endFill=1;html=1;strokeColor=#818CF8;strokeWidth=2;'
                     f'exitX=0.1;exitY=1;entryX=0.1;entryY=0;" edge="1" parent="1" source="{a}" target="{b}">'
                     f'<mxGeometry relative="1" as="geometry"/></mxCell>')
    for cid, x, y, w, h, icon, icol, title, lines, accent in CARDS:
        vertex(f'<b style="font-size:15px;color:#0F172A">{escape(title)}</b>'
               f'<br><br><span style="color:#475569">{"<br>".join(escape(l) for l in lines)}</span>',
               f"rounded=1;arcSize=14;html=1;fillColor=#FFFFFF;strokeColor=#E2E8F0;align=left;verticalAlign=top;"
               f"spacingLeft=18;spacingTop=14;fontSize=13;shadow=1;", x, y, w, h, cid=cid)
        vertex("", f"rounded=1;arcSize=50;fillColor={accent};strokeColor=none;", x, y + 16, 5, h - 32)
        image(icon, icol, x + 27, y + 25, 24)
    for pts, colour, label, dashed, *_ in EDGES:
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        mid = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in pts[1:-1])
        arr = f'<Array as="points">{mid}</Array>' if mid else ""
        dash = "dashed=1;" if dashed else ""
        cells.append(
            f'<mxCell id="{nid()}" value="{escape(label, quote=True)}" style="endArrow=block;endFill=1;html=1;rounded=1;'
            f'strokeColor={colour};strokeWidth=2.4;fontColor={colour};fontSize=12;fontStyle=1;'
            f'labelBackgroundColor=#FFFFFF;{dash}" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
            f'<mxPoint x="{x1}" y="{y1}" as="sourcePoint"/><mxPoint x="{x2}" y="{y2}" as="targetPoint"/>{arr}'
            f'</mxGeometry></mxCell>')
    model = (f'<mxGraphModel dx="{W}" dy="{H}" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
             f'fold="1" page="1" pageScale="1" pageWidth="{W}" pageHeight="{H}" background="#F8FAFC" math="0" '
             f'shadow="0"><root>{"".join(cells)}</root></mxGraphModel>')
    return model


if __name__ == "__main__":
    OUT_SVG.write_text(build_svg(), encoding="utf-8")
    model = build_drawio()
    OUT_DRAWIO.write_text(f'<mxfile host="propbot"><diagram id="propbot" name="propbot architecture">{model}'
                          f'</diagram></mxfile>\n', encoding="utf-8")
    USE_WEB_ICONS = True
    (HERE / "model_web.xml").write_text(build_drawio(), encoding="utf-8")
    print(f"wrote {OUT_SVG} and {OUT_DRAWIO}")

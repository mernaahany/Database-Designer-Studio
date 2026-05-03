"""
shared/erd/renderer.py
─────────────────────
Converts the structured ERD dict (from erd_data.extract_erd_data) into a
self-contained, interactive HTML string that Streamlit can display with
st.components.v1.html().

Design goals
------------
* Crows-foot notation for relationship lines (one / many / optional / mandatory)
* Colour-coded column badges: PK 🔑, FK 🔗, UNIQUE ◈, INDEX ⬡, NOT NULL ✦
* Draggable table cards — user can rearrange the diagram
* Auto-layout: tables placed in a grid, edges drawn as SVG bezier curves
* Live zoom + pan on the SVG canvas
* Responsive: fits the Streamlit column it's placed in
* A small legend panel in the corner
* Shows row counts and CHECK constraint count on each table card
* Views rendered in a distinct style (dashed border, italic name)

No external dependencies — pure HTML + CSS + vanilla JS.
"""

import json
import math
from typing import Any, Dict, List


#  Colour palette (matches app.py branding) 

PALETTE = {
    "bg":           "#F0F4F8",
    "canvas":       "#E8EDF3",
    "table_header": "#1E3A5F",
    "table_body":   "#FFFFFF",
    "table_border": "#2E86AB",
    "view_header":  "#5C6B7A",
    "view_border":  "#8FA8BC",
    "pk_bg":        "#FFF3CD",
    "pk_text":      "#7D5A00",
    "fk_bg":        "#D1ECF1",
    "fk_text":      "#0C5460",
    "unique_bg":    "#D4EDDA",
    "unique_text":  "#155724",
    "index_bg":     "#E2D9F3",
    "index_text":   "#4A2C8A",
    "nn_bg":        "#F8D7DA",
    "nn_text":      "#721C24",
    "edge_one":     "#2E86AB",
    "edge_many":    "#E06C00",
    "edge_dashed":  "#9AAFC0",
    "text_main":    "#1A1A2E",
    "text_dim":     "#6C757D",
    "legend_bg":    "rgba(255,255,255,0.92)",
    "shadow":       "rgba(30,58,95,0.13)",
}


#  Layout helpers
_CARD_W   = 240   # px width of each table card
_CARD_H_BASE  = 48   # header height
_ROW_H    = 24    # height per column row
_PAD_X    = 60    # horizontal gap between columns of cards
_PAD_Y    = 50    # vertical gap between rows of cards
_COLS     = 4     # cards per row in auto-layout


def _auto_positions(tables: List[Dict], views: List[Dict]) -> Dict[str, Dict]:
    """
    Assign (x, y) top-left positions to each table/view in a simple grid.
    Returns {"table_name": {"x": int, "y": int, "w": int, "h": int}, ...}
    """
    all_nodes = [(t["name"], len(t["columns"])) for t in tables]
    all_nodes += [(v["name"], 1) for v in views]

    positions = {}
    for i, (name, col_count) in enumerate(all_nodes):
        col = i % _COLS
        row = i // _COLS
        h   = _CARD_H_BASE + max(col_count, 1) * _ROW_H + 8
        positions[name] = {
            "x": col * (_CARD_W + _PAD_X) + _PAD_X,
            "y": row * (h      + _PAD_Y)  + _PAD_Y,
            "w": _CARD_W,
            "h": h,
        }
    return positions


def _edge_port(pos: Dict, side: str) -> Dict[str, float]:
    """
    Return the (cx, cy) connector point on the LEFT or RIGHT edge of a card,
    vertically centred on the header.
    """
    cy = pos["y"] + _CARD_H_BASE / 2
    if side == "right":
        return {"x": pos["x"] + pos["w"], "y": cy}
    return {"x": pos["x"], "y": cy}


#  SVG crows-foot markers 
# Encoded as SVG <path> snippets drawn at the tip of each relationship line.
# tick  = mandatory (|)   dashed circle = optional (o)
# single bar = "one"      crow = "many" (three lines)

def _svg_defs() -> str:
    """
    <defs> block: arrowhead and crows-foot marker definitions.
    We draw them manually at line endpoints in JS for flexibility,
    so we only need a base arrowhead for direction.
    """
    return """
  <defs>
    <marker id="arr-one"  markerWidth="10" markerHeight="10"
            refX="9" refY="5" orient="auto">
      <line x1="8" y1="1" x2="8" y2="9" stroke="{ec}" stroke-width="1.8"/>
      <line x1="6" y1="1" x2="6" y2="9" stroke="{ec}" stroke-width="1.8"/>
    </marker>
    <marker id="arr-many" markerWidth="12" markerHeight="12"
            refX="10" refY="6" orient="auto">
      <path d="M10,6 L2,1 M10,6 L2,6 M10,6 L2,11"
            stroke="{em}" stroke-width="1.8" fill="none"/>
    </marker>
    <filter id="card-shadow" x="-5%" y="-5%" width="110%" height="120%">
      <feDropShadow dx="0" dy="3" stdDeviation="4"
                    flood-color="{sh}" flood-opacity="1"/>
    </filter>
  </defs>
""".format(ec=PALETTE["edge_one"], em=PALETTE["edge_many"], sh=PALETTE["shadow"])


#  HTML template 

def _col_badges(col: Dict) -> str:
    """Build inline badge HTML for a column's flags."""
    badges = []
    if col["pk"]:
        badges.append(
            f'<span style="background:{PALETTE["pk_bg"]};color:{PALETTE["pk_text"]};'
            f'font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700;">PK</span>'
        )
    if col["fk_ref"]:
        badges.append(
            f'<span style="background:{PALETTE["fk_bg"]};color:{PALETTE["fk_text"]};'
            f'font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700;">FK</span>'
        )
    if col["unique"] and not col["pk"]:
        badges.append(
            f'<span style="background:{PALETTE["unique_bg"]};color:{PALETTE["unique_text"]};'
            f'font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700;">UQ</span>'
        )
    if col["indexed"] and not col["pk"] and not col["unique"]:
        badges.append(
            f'<span style="background:{PALETTE["index_bg"]};color:{PALETTE["index_text"]};'
            f'font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700;">IX</span>'
        )
    if col["notnull"] and not col["pk"]:
        badges.append(
            f'<span style="background:{PALETTE["nn_bg"]};color:{PALETTE["nn_text"]};'
            f'font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700;">NN</span>'
        )
    return " ".join(badges)


def _table_card_html(table: Dict, pos: Dict) -> str:
    """
    Render one draggable table card as an absolutely-positioned <div>.
    """
    name      = table["name"]
    columns   = table["columns"]
    row_count = table["row_count"]
    checks    = table["checks"]

    #  Header 
    rc_badge = (
        f'<span style="background:rgba(255,255,255,0.2);color:#cde;'
        f'font-size:9px;padding:1px 6px;border-radius:10px;margin-left:6px;">'
        f'{row_count} rows</span>'
    )
    ck_badge = ""
    if checks:
        ck_badge = (
            f'<span style="background:rgba(255,200,0,0.25);color:#ffe;'
            f'font-size:9px;padding:1px 6px;border-radius:10px;margin-left:4px;" '
            f'title="{"; ".join(checks)}">'
            f'✓ {len(checks)} CHECK</span>'
        )

    header = (
        f'<div style="background:{PALETTE["table_header"]};color:white;'
        f'padding:6px 10px;border-radius:6px 6px 0 0;'
        f'font-size:12px;font-weight:700;font-family:\'Courier New\',monospace;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
        f'display:flex;align-items:center;gap:2px;" title="{name}">'
        f'🗂 {name}{rc_badge}{ck_badge}'
        f'</div>'
    )


    col_rows = []
    for col in columns:
        badges   = _col_badges(col)
        type_str = col["type"] or "TEXT"
        dflt_str = f' <span style="color:{PALETTE["text_dim"]};font-size:9px;">'  \
                   f'= {col["default"]}</span>' if col["default"] is not None else ""
        fk_tip   = f' title="FK → {col["fk_ref"]}"' if col["fk_ref"] else ""

        # bold PK column names
        name_style = (
            f'font-weight:700;color:{PALETTE["pk_text"]};' if col["pk"]
            else f'color:{PALETTE["text_main"]};'
        )
        col_rows.append(
            f'<div style="display:flex;align-items:center;gap:4px;'
            f'padding:2px 8px;border-bottom:1px solid #EEF0F3;'
            f'font-family:\'Courier New\',monospace;font-size:10px;'
            f'min-height:{_ROW_H}px;box-sizing:border-box;" {fk_tip}>'
            f'<span style="{name_style}white-space:nowrap;overflow:hidden;'
            f'text-overflow:ellipsis;flex:1 1 auto;">{col["name"]}</span>'
            f'<span style="color:{PALETTE["text_dim"]};white-space:nowrap;'
            f'font-size:9px;flex:0 0 auto;">{type_str}</span>'
            f'{dflt_str}'
            f'<span style="flex:0 0 auto;">{badges}</span>'
            f'</div>'
        )
    body = "".join(col_rows)

    #  Indexes footer (if any non-auto indexes)
    idx_footer = ""
    for idx in table.get("indexes", []):
        u_label = "UNIQUE " if idx["unique"] else ""
        idx_footer += (
            f'<div style="font-size:9px;color:{PALETTE["text_dim"]};'
            f'padding:1px 8px;font-family:\'Courier New\',monospace;">'
            f'⬡ {u_label}INDEX {idx["name"]} ({", ".join(idx["columns"])})'
            f'</div>'
        )

    return (
        f'<div class="erd-card" id="card-{name}" '
        f'data-table="{name}" '
        f'style="position:absolute;left:{pos["x"]}px;top:{pos["y"]}px;'
        f'width:{pos["w"]}px;'
        f'background:{PALETTE["table_body"]};'
        f'border:2px solid {PALETTE["table_border"]};'
        f'border-radius:7px;'
        f'box-shadow:0 3px 12px {PALETTE["shadow"]};'
        f'cursor:grab;user-select:none;z-index:10;">'
        f'{header}{body}{idx_footer}'
        f'</div>'
    )


def _view_card_html(view: Dict, pos: Dict) -> str:
    """
    Render a VIEW as a dashed-border card — visually distinct from tables.
    """
    name = view["name"]
    header = (
        f'<div style="background:{PALETTE["view_header"]};color:white;'
        f'padding:6px 10px;border-radius:5px 5px 0 0;'
        f'font-size:12px;font-weight:700;font-style:italic;'
        f'font-family:\'Courier New\',monospace;">'
        f'👁 {name} <span style="font-size:9px;opacity:.7;">(VIEW)</span>'
        f'</div>'
        f'<div style="padding:4px 8px;font-size:9px;color:{PALETTE["text_dim"]};'
        f'font-family:\'Courier New\',monospace;word-break:break-all;'
        f'max-height:80px;overflow:hidden;">{view["sql"]}</div>'
    )
    return (
        f'<div class="erd-card" id="card-{name}" '
        f'data-table="{name}" '
        f'style="position:absolute;left:{pos["x"]}px;top:{pos["y"]}px;'
        f'width:{pos["w"]}px;'
        f'background:{PALETTE["table_body"]};'
        f'border:2px dashed {PALETTE["view_border"]};'
        f'border-radius:7px;'
        f'box-shadow:0 2px 8px {PALETTE["shadow"]};'
        f'cursor:grab;user-select:none;z-index:10;">'
        f'{header}'
        f'</div>'
    )


def _legend_html() -> str:
    """Small fixed legend panel."""
    items = [
        (PALETTE["pk_bg"],     PALETTE["pk_text"],     "PK",  "Primary Key"),
        (PALETTE["fk_bg"],     PALETTE["fk_text"],     "FK",  "Foreign Key"),
        (PALETTE["unique_bg"], PALETTE["unique_text"],  "UQ",  "Unique"),
        (PALETTE["index_bg"],  PALETTE["index_text"],   "IX",  "Indexed"),
        (PALETTE["nn_bg"],     PALETTE["nn_text"],      "NN",  "Not Null"),
    ]
    badges_html = "".join(
        f'<span style="background:{bg};color:{fg};font-size:9px;'
        f'padding:1px 6px;border-radius:3px;font-weight:700;margin-right:4px;">'
        f'{label}</span><span style="font-size:10px;color:#444;">{desc}</span><br>'
        for bg, fg, label, desc in items
    )
    rel_lines = (
        f'<div style="margin-top:6px;font-size:10px;color:#444;">'
        f'<span style="color:{PALETTE["edge_one"]};font-weight:700;">──|</span>'
        f'  "One" side&nbsp;&nbsp;'
        f'<span style="color:{PALETTE["edge_many"]};font-weight:700;">&lt;&lt;</span>'
        f'  "Many" side'
        f'</div>'
        f'<div style="font-size:10px;color:#444;">'
        f'<span style="color:#888;font-style:italic;">○ dashed</span>'
        f' = optional FK (nullable)'
        f'</div>'
    )
    return (
        f'<div id="erd-legend" style="position:fixed;bottom:18px;right:18px;'
        f'background:{PALETTE["legend_bg"]};'
        f'border:1px solid #D0D8E4;border-radius:8px;padding:10px 14px;'
        f'box-shadow:0 2px 10px rgba(0,0,0,.12);z-index:200;min-width:190px;">'
        f'<div style="font-size:11px;font-weight:700;color:{PALETTE["table_header"]};'
        f'margin-bottom:6px;">🗺 Legend</div>'
        f'{badges_html}'
        f'{rel_lines}'
        f'</div>'
    )


#  Main public function 

def render_erd_html(erd_data: Dict[str, Any], height: int = 620) -> str:
    """
    Convert an ERD data dict (from erd_data.extract_erd_data) into a
    self-contained HTML string ready for st.components.v1.html().

    Parameters
    ----------
    erd_data : Output of extract_erd_data().
    height   : Desired height of the rendered component in pixels.

    Returns
    -------
    A complete HTML document string.
    """
    tables        = erd_data["tables"]
    views         = erd_data["views"]
    relationships = erd_data["relationships"]

    if not tables and not views:
        return (
            "<div style='padding:2rem;text-align:center;color:#6c757d;'>"
            "<div style='font-size:2rem;'>📭</div>"
            "<div>No tables found — create some tables first via the Chat tab.</div>"
            "</div>"
        )

    #  Auto-layout 
    positions = _auto_positions(tables, views)

    # Compute canvas size based on rightmost / bottommost card
    canvas_w = max(
        (p["x"] + p["w"] + _PAD_X) for p in positions.values()
    ) if positions else 800
    canvas_h = max(
        (p["y"] + p["h"] + _PAD_Y) for p in positions.values()
    ) if positions else 600

    #  Card HTML 
    cards_html = ""
    for tbl in tables:
        pos = positions.get(tbl["name"])
        if pos:
            cards_html += _table_card_html(tbl, pos)
    for vw in views:
        pos = positions.get(vw["name"])
        if pos:
            cards_html += _view_card_html(vw, pos)

    #  Relationships JSON for JS renderer 
    rel_json = json.dumps(relationships)

    #  Positions JSON for JS 
    pos_json = json.dumps(positions)

    #  Colours for JS 
    clr_one  = PALETTE["edge_one"]
    clr_many = PALETTE["edge_many"]
    clr_dash = PALETTE["edge_dashed"]

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {PALETTE["canvas"]};
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }}
  #erd-wrapper {{
    width: 100%;
    height: {height}px;
    overflow: hidden;
    position: relative;
    background:
      radial-gradient(circle, #c8d6e5 1px, transparent 1px) 0 0 / 22px 22px,
      {PALETTE["canvas"]};
  }}
  #erd-canvas {{
    position: absolute;
    transform-origin: 0 0;
    top: 0; left: 0;
  }}
  #erd-svg {{
    position: absolute;
    top: 0; left: 0;
    pointer-events: none;
    overflow: visible;
  }}
  .erd-card:hover {{
    box-shadow: 0 6px 24px rgba(30,58,95,0.22) !important;
    z-index: 20 !important;
  }}
  /* Toolbar */
  #erd-toolbar {{
    position: absolute;
    top: 10px;
    left: 10px;
    display: flex;
    gap: 6px;
    z-index: 100;
  }}
  .erd-btn {{
    background: white;
    border: 1px solid #C8D6E5;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
    cursor: pointer;
    color: {PALETTE["table_header"]};
    font-weight: 600;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
  }}
  .erd-btn:hover {{ background: {PALETTE["pk_bg"]}; }}
</style>
</head>
<body>

<div id="erd-wrapper">
  <div id="erd-toolbar">
    <button class="erd-btn" onclick="zoomIn()">＋ Zoom</button>
    <button class="erd-btn" onclick="zoomOut()">－ Zoom</button>
    <button class="erd-btn" onclick="resetView()">⟲ Reset</button>
    <button class="erd-btn" onclick="fitAll()">⊡ Fit All</button>
  </div>

  <div id="erd-canvas" style="width:{canvas_w}px;height:{canvas_h}px;">
    <!-- SVG layer for relationship lines (drawn behind cards) -->
    <svg id="erd-svg" width="{canvas_w}" height="{canvas_h}"></svg>
    <!-- Table cards -->
    {cards_html}
  </div>
</div>

{_legend_html()}

<script>
// ── State ──────────────────────────────────────────────────────────────────
const RELATIONSHIPS = {rel_json};
const POSITIONS     = {pos_json};
const CLR_ONE  = "{clr_one}";
const CLR_MANY = "{clr_many}";
const CLR_DASH = "{clr_dash}";

// Card positions are tracked in a mutable registry so drag moves update edges
const cardPos = {{}};
Object.entries(POSITIONS).forEach(([name, p]) => {{
  cardPos[name] = {{ x: p.x, y: p.y, w: p.w, h: p.h }};
}});

// ── Zoom / Pan ─────────────────────────────────────────────────────────────
let scale    = 1.0;
let panX     = 0;
let panY     = 0;
let isPanning = false;
let panStart = null;
const wrapper = document.getElementById("erd-wrapper");
const canvas  = document.getElementById("erd-canvas");

function applyTransform() {{
  canvas.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{scale}})`;
}}

function zoomIn()   {{ scale = Math.min(scale * 1.2, 4);   applyTransform(); }}
function zoomOut()  {{ scale = Math.max(scale / 1.2, 0.2); applyTransform(); }}
function resetView() {{ scale = 1; panX = 0; panY = 0; applyTransform(); }}
function fitAll() {{
  const wrapRect = wrapper.getBoundingClientRect();
  const allCards = document.querySelectorAll(".erd-card");
  if (!allCards.length) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  allCards.forEach(c => {{
    const l = parseInt(c.style.left);
    const t = parseInt(c.style.top);
    const w = parseInt(c.style.width);
    const h = c.offsetHeight;
    minX = Math.min(minX, l); minY = Math.min(minY, t);
    maxX = Math.max(maxX, l + w); maxY = Math.max(maxY, t + h);
  }});
  const contentW = maxX - minX + 80;
  const contentH = maxY - minY + 80;
  scale = Math.min(wrapRect.width / contentW, wrapRect.height / contentH, 1.5);
  panX  = (wrapRect.width  - contentW * scale) / 2 - minX * scale + 40 * scale;
  panY  = (wrapRect.height - contentH * scale) / 2 - minY * scale + 40 * scale;
  applyTransform();
}}

// Mouse-wheel zoom centred on cursor
wrapper.addEventListener("wheel", e => {{
  e.preventDefault();
  const rect  = wrapper.getBoundingClientRect();
  const mx    = e.clientX - rect.left;
  const my    = e.clientY - rect.top;
  const delta = e.deltaY < 0 ? 1.1 : 0.91;
  panX  = mx - (mx - panX) * delta;
  panY  = my - (my - panY) * delta;
  scale = Math.min(Math.max(scale * delta, 0.2), 4);
  applyTransform();
}}, {{ passive: false }});

// Middle-mouse / Space+drag pan
wrapper.addEventListener("mousedown", e => {{
  if (e.button === 1 || e.spaceKey) {{
    isPanning = true;
    panStart  = {{ x: e.clientX - panX, y: e.clientY - panY }};
    wrapper.style.cursor = "grabbing";
  }}
}});
wrapper.addEventListener("mousemove", e => {{
  if (!isPanning) return;
  panX = e.clientX - panStart.x;
  panY = e.clientY - panStart.y;
  applyTransform();
}});
wrapper.addEventListener("mouseup",    () => {{ isPanning = false; wrapper.style.cursor = ""; }});
wrapper.addEventListener("mouseleave", () => {{ isPanning = false; }});


// ── Drag cards ────────────────────────────────────────────────────────────
let dragging   = null;
let dragOffset = {{ x: 0, y: 0 }};

document.querySelectorAll(".erd-card").forEach(card => {{
  card.addEventListener("mousedown", e => {{
    if (e.button !== 0) return;
    e.stopPropagation();
    dragging   = card;
    const rect = card.getBoundingClientRect();
    dragOffset = {{
      x: (e.clientX - rect.left) / scale,
      y: (e.clientY - rect.top)  / scale,
    }};
    card.style.zIndex = "50";
    card.style.cursor = "grabbing";
  }});
}});

document.addEventListener("mousemove", e => {{
  if (!dragging) return;
  const wrapRect = wrapper.getBoundingClientRect();
  const nx = (e.clientX - wrapRect.left - panX) / scale - dragOffset.x;
  const ny = (e.clientY - wrapRect.top  - panY) / scale - dragOffset.y;
  dragging.style.left = nx + "px";
  dragging.style.top  = ny + "px";
  const name = dragging.dataset.table;
  cardPos[name].x = nx;
  cardPos[name].y = ny;
  drawEdges();
}});

document.addEventListener("mouseup", () => {{
  if (dragging) {{
    dragging.style.zIndex = "10";
    dragging.style.cursor = "grab";
    dragging = null;
  }}
}});


// ── Edge / relationship drawing ────────────────────────────────────────────
const svg = document.getElementById("erd-svg");

function getPort(tableName, side) {{
  const p = cardPos[tableName];
  if (!p) return null;
  const el  = document.getElementById("card-" + tableName);
  const h   = el ? el.offsetHeight : p.h;
  const cy  = p.y + 24; // vertically at header midpoint
  return {{
    x: side === "right" ? p.x + p.w : p.x,
    y: cy,
  }};
}}

function crowsFoot(x, y, dir, isMany, isOptional, color) {{
  // dir: "left" | "right" — direction of the line arriving at this point
  const d    = dir === "right" ? 1 : -1;   // outward direction
  const R    = 7;   // radius
  const arms = [];

  if (isOptional) {{
    // draw a small circle for optional participation
    arms.push(`<circle cx="${{x + d * (R + 6)}}" cy="${{y}}" r="4"
               stroke="${{color}}" stroke-width="1.5" fill="white"/>`);
  }} else {{
    // mandatory tick |
    arms.push(`<line x1="${{x + d * R}}" y1="${{y - 6}}"
                     x2="${{x + d * R}}" y2="${{y + 6}}"
               stroke="${{color}}" stroke-width="2"/>`);
  }}

  if (isMany) {{
    // crow's foot — three diverging lines
    arms.push(
      `<line x1="${{x}}" y1="${{y}}" x2="${{x + d * R}}" y2="${{y - 7}}"
             stroke="${{color}}" stroke-width="1.8"/>`,
      `<line x1="${{x}}" y1="${{y}}" x2="${{x + d * R}}" y2="${{y}}"
             stroke="${{color}}" stroke-width="1.8"/>`,
      `<line x1="${{x}}" y1="${{y}}" x2="${{x + d * R}}" y2="${{y + 7}}"
             stroke="${{color}}" stroke-width="1.8"/>`,
    );
  }} else {{
    // single bar — one line
    arms.push(`<line x1="${{x + d * 2}}" y1="${{y - 6}}"
                     x2="${{x + d * 2}}" y2="${{y + 6}}"
               stroke="${{color}}" stroke-width="2"/>`);
  }}
  return arms.join("");
}}

function drawEdges() {{
  let markup = "";

  RELATIONSHIPS.forEach(rel => {{
    const fromPos = cardPos[rel.from_table];
    const toPos   = cardPos[rel.to_table];
    if (!fromPos || !toPos) return;

    // Choose sides: if "to" is to the right of "from", from exits right, to enters left
    const fromCx = fromPos.x + fromPos.w / 2;
    const toCx   = toPos.x  + toPos.w  / 2;

    const fromSide = fromCx <= toCx ? "right" : "left";
    const toSide   = fromCx <= toCx ? "left"  : "right";

    const from = getPort(rel.from_table, fromSide);
    const to   = getPort(rel.to_table,   toSide);
    if (!from || !to) return;

    // Bezier control points
    const dx   = Math.abs(to.x - from.x) * 0.45 + 30;
    const cpx1 = from.x + (fromSide === "right" ?  dx : -dx);
    const cpx2 = to.x   + (toSide   === "right" ?  dx : -dx);

    const isOptional = rel.optional;
    const isMany     = rel.from_card === "N";
    const color      = isMany ? CLR_MANY : CLR_ONE;
    const dashAttr   = isOptional ? 'stroke-dasharray="5,3"' : '';

    // Path line
    markup += `<path d="M${{from.x}},${{from.y}}
                         C${{cpx1}},${{from.y}} ${{cpx2}},${{to.y}} ${{to.x}},${{to.y}}"
               fill="none" stroke="${{color}}" stroke-width="1.8"
               ${{dashAttr}} opacity="0.85"/>`;

    // Tooltip rect (invisible, wide click target)
    const midX = (from.x + to.x) / 2;
    const midY = (from.y + to.y) / 2;
    markup += `<text x="${{midX}}" y="${{midY - 4}}"
                     font-size="9" fill="${{color}}" text-anchor="middle"
                     font-family="Courier New,monospace" opacity="0.8">
                 ${{rel.from_col}} → ${{rel.to_table}}.${{rel.to_col}}
               </text>`;

    // Crows-foot at "from" (the many/child side)
    markup += crowsFoot(from.x, from.y, fromSide === "right" ? "left" : "right",
                        isMany, isOptional, color);

    // Single bar at "to" (the one/parent side)
    markup += crowsFoot(to.x, to.y, toSide === "right" ? "left" : "right",
                        false, false, CLR_ONE);
  }});

  svg.innerHTML = markup;
}}

// Initial draw
window.addEventListener("load", () => {{
  drawEdges();
  // Brief delay so card heights are measured
  setTimeout(drawEdges, 150);
}});
</script>
</body>
</html>"""

    return html
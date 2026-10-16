"""
Render the report as one self-contained HTML page (Sage theme, A4 print layout).

- Jinja2 with autoescaping: column names and cell values come from user files,
  so every one of them is escaped. Charts are SVG built here with escape() too.
- No JavaScript at all, so even a hostile column name can't run anything.
- "Save as PDF" is the browser's print dialog; the print stylesheet handles page
  breaks, so no PDF library (and no extra memory) is needed on the server.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

TEMPLATES = Path(__file__).parent / "templates"

# Sage chart colours, validated for contrast on the light report surface.
BAR = "#4A8C5C"
GRID = "#E8ECE5"
AXIS = "#59645B"

_env = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _column_chart(bars: list[dict], first_label: str, last_label: str, caption: str) -> Markup:
    width, height, pad_left, pad_bottom = 520, 170, 34, 22
    plot_w, plot_h = width - pad_left - 4, height - pad_bottom - 8
    peak = max((b["value"] for b in bars), default=0) or 1
    gap = 2
    bw = plot_w / max(len(bars), 1)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{escape(caption)}" class="chart">',
        f'<line x1="{pad_left}" y1="8" x2="{width - 4}" y2="8" stroke="{GRID}"/>',
        f'<line x1="{pad_left}" y1="{8 + plot_h / 2:.1f}" x2="{width - 4}" y2="{8 + plot_h / 2:.1f}" stroke="{GRID}"/>',
        f'<text x="{pad_left - 6}" y="12" text-anchor="end" class="tick">{peak}</text>',
        f'<text x="{pad_left - 6}" y="{8 + plot_h + 4}" text-anchor="end" class="tick">0</text>',
    ]
    for i, bar in enumerate(bars):
        h = plot_h * bar["value"] / peak
        x = pad_left + i * bw + gap / 2
        y = 8 + plot_h - h
        w = max(bw - gap, 1)
        r = min(4, w / 2, h) if h else 0
        if h:
            parts.append(
                f'<path d="M{x:.1f},{8 + plot_h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
                f'H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{8 + plot_h:.1f} Z" fill="{BAR}">'
                f'<title>{escape(bar["label"])}: {bar["value"]}</title></path>'
            )
    parts += [
        f'<line x1="{pad_left}" y1="{8 + plot_h}" x2="{width - 4}" y2="{8 + plot_h}" stroke="{AXIS}" stroke-opacity="0.5"/>',
        f'<text x="{pad_left}" y="{height - 4}" class="tick">{escape(first_label)}</text>',
        f'<text x="{width - 4}" y="{height - 4}" text-anchor="end" class="tick">{escape(last_label)}</text>',
        "</svg>",
    ]
    return Markup("".join(parts))


def _bar_list(bars: list[dict], other: int) -> Markup:
    peak = max((b["value"] for b in bars), default=0) or 1
    rows = []
    for bar in bars:
        width = 100 * bar["value"] / peak
        rows.append(
            '<div class="hbar"><span class="hbar-label">{label}</span>'
            '<span class="hbar-track"><span class="hbar-fill" style="width:{w:.1f}%"></span></span>'
            '<span class="hbar-value">{value}</span></div>'.format(label=escape(bar["label"]), w=width, value=bar["value"])
        )
    if other:
        rows.append(f'<div class="hbar-other">+ {other} more row(s) in other values</div>')
    return Markup("".join(rows))


def chart_svg(chart: dict | None) -> Markup:
    if not chart or not chart.get("bars"):
        return Markup("")
    bars = chart["bars"]
    if chart["kind"] == "histogram":
        from app.report.builder import fmt_num
        return _column_chart(bars, fmt_num(bars[0]["start"]), fmt_num(bars[-1]["end"]), "Histogram")
    if chart["kind"] == "timeline":
        return _column_chart(bars, bars[0]["label"], bars[-1]["label"], f"Rows per period ({chart.get('period')})")
    return _bar_list(bars, chart.get("other", 0))


_env.globals["chart_svg"] = chart_svg


def render_html(report: dict) -> str:
    return _env.get_template("report.html.j2").render(r=report)


# Served with the HTML so that, even if it is opened from the API origin, the
# page can load only its fonts and inline styles: no scripts, no other requests.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src data:; base-uri 'none'; form-action 'none'"
)

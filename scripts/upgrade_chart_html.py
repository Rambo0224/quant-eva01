from __future__ import annotations

import re
from pathlib import Path


CHARTS_DIR = Path(__file__).resolve().parents[1] / "charts"
INTERACTIVE_CHART_WIDTH = 1358
INTERACTIVE_CHART_HEIGHT = 900
INTERACTIVE_CHART_ASPECT = f"{INTERACTIVE_CHART_WIDTH} / {INTERACTIVE_CHART_HEIGHT}"

DIV_RE = re.compile(
    r'<div id="(?P<id>[^"]+)" class="plotly-graph-div" style="(?P<style>[^"]*)"></div>'
)
STYLE_RE = re.compile(r'<style id="fullpage-plotly">.*?</style>', re.DOTALL)
SCRIPT_RE = re.compile(r'<script id="fullpage-plotly-resize">.*?</script>', re.DOTALL)
CHART_ROOT_STYLE_RE = re.compile(r"#chart-root \{[^}]*\}")


AUTO_TIME_AXIS_SCRIPT = (
    "    function applyResponsiveTimeAxis() {\n"
    "      figure.layout = figure.layout || {};\n"
    "      figure.layout.xaxis = figure.layout.xaxis || {};\n"
    "      const xaxis = figure.layout.xaxis;\n"
    "      delete xaxis.dtick;\n"
    "      delete xaxis.tickangle;\n"
    "      delete xaxis.ticklabelmode;\n"
    "      const width = Math.max(360, chartRoot ? chartRoot.clientWidth : 900);\n"
    "      const maxTicks = Math.max(4, Math.min(14, Math.round(width / 150)));\n"
    "      const timestamps = [];\n"
    "      (figure.data || []).forEach(function(trace) {\n"
    "        (trace.x || []).forEach(function(value) {\n"
    "          const time = Date.parse(value);\n"
    "          if (!Number.isNaN(time)) timestamps.push(time);\n"
    "        });\n"
    "      });\n"
    "      if (timestamps.length) {\n"
    "        const spanDays = Math.max(1, (Math.max.apply(null, timestamps) - Math.min.apply(null, timestamps)) / 86400000);\n"
    "        xaxis.nticks = spanDays <= 30 ? Math.max(4, Math.min(maxTicks, Math.round(spanDays) + 1)) : maxTicks;\n"
    "        xaxis.tickformat = spanDays <= 75 ? '%Y-%m-%d' : (spanDays <= 900 ? '%Y-%m' : '%Y');\n"
    "      } else {\n"
    "        xaxis.nticks = maxTicks;\n"
    "      }\n"
    "      xaxis.tickmode = 'auto';\n"
    "    }\n"
)


def _target_chart_root_style() -> str:
    return (
        f"#chart-root {{ width: min(100vw, calc(100vh * {INTERACTIVE_CHART_WIDTH} / {INTERACTIVE_CHART_HEIGHT})); "
        f"aspect-ratio: {INTERACTIVE_CHART_ASPECT}; visibility: hidden; }}"
    )


def upgrade_html(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False

    legacy_config = '{"displayModeBar": false, "responsive": true}'
    upgraded_config = '{"displayModeBar": true, "displaylogo": false, "responsive": true, "scrollZoom": true}'
    if legacy_config in text:
        text = text.replace(legacy_config, upgraded_config)
        changed = True

    text, style_removed = STYLE_RE.subn("", text)
    text, script_removed = SCRIPT_RE.subn("", text)
    changed = changed or bool(style_removed or script_removed)

    if 'id="chart-root"' in text:
        text, root_style_replacements = CHART_ROOT_STYLE_RE.subn(_target_chart_root_style(), text)
        changed = changed or bool(root_style_replacements)
        replacements = {
            "body { overflow: hidden; }": "body { overflow: hidden; display: flex; align-items: flex-start; justify-content: center; }",
            "figure.layout.autosize = true;": "figure.layout.autosize = false;",
        }
        for old, new in replacements.items():
            if old in text:
                text = text.replace(old, new)
                changed = True
        if "function applyResponsiveTimeAxis()" not in text and "    function renderChart() {\n" in text:
            text = text.replace("    function renderChart() {\n", AUTO_TIME_AXIS_SCRIPT + "    function renderChart() {\n", 1)
            changed = True
        if "      figure.layout.height = chartRoot.clientHeight;\n      applyResponsiveTimeAxis();\n" not in text:
            marker = "      figure.layout.height = chartRoot.clientHeight;\n"
            if marker in text:
                text = text.replace(marker, marker + "      applyResponsiveTimeAxis();\n", 1)
                changed = True
        if changed:
            path.write_text(text, encoding="utf-8")
        return changed

    match = DIV_RE.search(text)
    if not match:
        if changed:
            path.write_text(text, encoding="utf-8")
        return changed

    div_id = match.group("id")
    text = DIV_RE.sub(
        f'<div id="{div_id}" class="plotly-graph-div"></div>',
        text,
        count=1,
    )
    changed = True

    if "<head>" in text and 'name="viewport"' not in text:
        text = text.replace('<head>', '<head><meta name="viewport" content="width=device-width, initial-scale=1" />', 1)
        changed = True

    if "<head>" in text:
        text = text.replace(
            "</head>",
            (
                "<style id=\"fullpage-plotly\">"
                "html, body { width: 100%; height: 100%; margin: 0; background: #ffffff; } "
                "body { overflow: hidden; display: flex; align-items: flex-start; justify-content: center; } "
                f"#{div_id} {{ width: min(100vw, calc(100vh * {INTERACTIVE_CHART_WIDTH} / {INTERACTIVE_CHART_HEIGHT})) !important; "
                f"aspect-ratio: {INTERACTIVE_CHART_ASPECT}; visibility: hidden; }}"
                "</style></head>"
            ),
            1,
        )
        changed = True

    resize_script = (
        "<script id=\"fullpage-plotly-resize\">"
        "(function(){"
        f"const plotId = {div_id!r};"
        "function fit(){"
        "const el = document.getElementById(plotId);"
        "if (!el || !window.Plotly) return;"
        "const width = el.clientWidth;"
        "const height = el.clientHeight;"
        "const maxTicks = Math.max(4, Math.min(14, Math.round(Math.max(360, width) / 150)));"
        "Plotly.relayout(el, {autosize: false, width: width, height: height, 'xaxis.tickmode': 'auto', 'xaxis.nticks': maxTicks}).then(function(){"
        "el.style.visibility = 'visible';"
        "});"
        "}"
        "window.addEventListener('load', function(){ setTimeout(fit, 0); setTimeout(fit, 250); });"
        "setTimeout(fit, 0);"
        "window.addEventListener('resize', fit);"
        "})();"
        "</script>"
    )

    if "</body>" in text:
        text = text.replace("</body>", resize_script + "</body>", 1)
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def main() -> None:
    upgraded = 0
    skipped = 0
    for path in sorted(CHARTS_DIR.glob("*.html")):
        if upgrade_html(path):
            upgraded += 1
            print(f"[INFO] Upgraded {path.name}")
        else:
            skipped += 1
            print(f"[SKIP] {path.name}")
    print(f"[DONE] upgraded={upgraded} skipped={skipped}")


if __name__ == "__main__":
    main()

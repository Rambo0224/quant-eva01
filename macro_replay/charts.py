from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime
from pathlib import Path
from typing import Tuple

import plotly
import plotly.express as px
import plotly.graph_objects as go

from .source_labels import display_source_label
from .data_quality import chart_data_quality


LINE_TRACE_MODE = "lines"
INTERACTIVE_CHART_WIDTH = 1358
INTERACTIVE_CHART_HEIGHT = 900
INTERACTIVE_CHART_ASPECT = f"{INTERACTIVE_CHART_WIDTH} / {INTERACTIVE_CHART_HEIGHT}"


class ChartImageExportWarning(RuntimeWarning):
    """Image export raised during browser cleanup after the PNG was written."""


def _write_compact_image(fig: go.Figure, image_path: Path) -> None:
    """Write the thumbnail PNG and tolerate Kaleido cleanup noise only when safe.

    Plotly/Kaleido can occasionally raise "Couldn't close or kill browser
    subprocess" after the PNG file has already been created. That should not
    make the whole data refresh fail, but a missing or empty image is still a
    real failure and must bubble up.
    """
    image_path.parent.mkdir(parents=True, exist_ok=True)
    figure_json = plotly.io.to_json(fig)
    export_code = """
from __future__ import annotations
from pathlib import Path
import sys
import plotly.io as pio

figure_path = Path(sys.argv[1])
image_path = Path(sys.argv[2])
fig = pio.from_json(figure_path.read_text(encoding="utf-8"))
fig.write_image(image_path)
""".strip()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".plotly.json", encoding="utf-8", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(figure_json)
        result = subprocess.run(
            [sys.executable, "-c", export_code, str(temp_path), str(image_path)],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        if image_path.exists() and image_path.stat().st_size > 0:
            warnings.warn(
                "PNG thumbnail was written, but image exporter timed out during cleanup.",
                ChartImageExportWarning,
                stacklevel=2,
            )
            return
        raise TimeoutError(f"PNG image export timed out after {exc.timeout} seconds: {image_path}") from exc
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        if image_path.exists() and image_path.stat().st_size > 0:
            warnings.warn(
                f"PNG thumbnail was written, but image exporter exited with code {result.returncode}: {stderr}",
                ChartImageExportWarning,
                stacklevel=2,
            )
            return
        raise RuntimeError(f"PNG image export failed for {image_path}: {stderr}")

    if not image_path.exists() or image_path.stat().st_size <= 0:
        raise RuntimeError(f"PNG image export finished without creating a non-empty file: {image_path}")


def _estimate_time_tick_count(df, x_field: str, compact: bool) -> int | None:
    if x_field not in df.columns:
        return None
    plot_dates = None
    try:
        import pandas as pd

        plot_dates = pd.to_datetime(df[x_field], errors="coerce").dropna()
    except Exception:
        return None
    if plot_dates is None or plot_dates.empty:
        return None
    span_days = int(max(1, (plot_dates.max() - plot_dates.min()).days))
    width = INTERACTIVE_CHART_WIDTH if not compact else 420
    base = max(4, min(12 if not compact else 7, int(round(width / (160 if not compact else 90)))))
    if span_days <= 30:
        return max(4, min(span_days + 1, base))
    if span_days <= 120:
        return max(4, min(8 if compact else 10, base))
    if span_days <= 365:
        return max(4, min(10 if compact else 12, base))
    if span_days <= 730:
        return max(4, min(12 if compact else 14, base))
    return max(4, min(14, base))


def _estimate_time_tickformat(df, x_field: str) -> str | None:
    if x_field not in df.columns:
        return None
    try:
        import pandas as pd

        plot_dates = pd.to_datetime(df[x_field], errors="coerce").dropna()
    except Exception:
        return None
    if plot_dates is None or plot_dates.empty:
        return None
    span_days = int(max(1, (plot_dates.max() - plot_dates.min()).days))
    if span_days <= 75:
        return "%Y-%m-%d"
    if span_days <= 900:
        return "%Y-%m"
    return "%Y"


def _build_base_figure(df, indicator) -> tuple[go.Figure, str, str]:
    chart_cfg = indicator.chart or {}
    chart_type = chart_cfg.get("type", "line")
    x_field = chart_cfg.get("x_field", "date")
    y_field = chart_cfg.get("y_field", "value")
    title = chart_cfg.get("title", indicator.title)
    labels = {
        x_field: chart_cfg.get("x_label", ""),
        y_field: chart_cfg.get("y_label", y_field),
    }

    if chart_type == "term_structure":
        color_field = chart_cfg.get("color_field", "curve_label")
        category_field = chart_cfg.get("category_field", x_field)
        category_order = list(dict.fromkeys(df[category_field].tolist()))
        fig = px.line(
            df,
            x=x_field,
            y=y_field,
            color=color_field,
            template="plotly_white",
            category_orders={category_field: category_order},
            render_mode="svg",
        )
        fig.update_traces(mode=LINE_TRACE_MODE, connectgaps=True, line=dict(width=1.8))
    elif chart_type == "multi_axis_line":
        fig = go.Figure()
        field_labels = chart_cfg.get("field_labels", {})
        field_colors = chart_cfg.get("field_colors", {})
        left_fields = chart_cfg.get("left_fields", [])
        right_fields = chart_cfg.get("right_fields", [])
        for field in left_fields:
            if field not in df.columns:
                continue
            fig.add_trace(
                go.Scatter(
                    x=df[x_field],
                    y=df[field],
                    mode=LINE_TRACE_MODE,
                    connectgaps=True,
                    name=field_labels.get(field, field),
                    line=dict(width=1.8, color=field_colors.get(field)),
                    yaxis="y",
                )
            )
        for field in right_fields:
            if field not in df.columns:
                continue
            fig.add_trace(
                go.Scatter(
                    x=df[x_field],
                    y=df[field],
                    mode=LINE_TRACE_MODE,
                    connectgaps=True,
                    name=field_labels.get(field, field),
                    line=dict(width=1.8, color=field_colors.get(field)),
                    yaxis="y2",
                )
            )
    elif chart_type == "stacked_area":
        fig = go.Figure()
        field_labels = chart_cfg.get("field_labels", {})
        field_colors = chart_cfg.get("field_colors", {})
        stack_fields = chart_cfg.get("stack_fields", [])
        for field in stack_fields:
            if field not in df.columns:
                continue
            fig.add_trace(
                go.Scatter(
                    x=df[x_field],
                    y=df[field],
                    mode=LINE_TRACE_MODE,
                    connectgaps=True,
                    name=field_labels.get(field, field),
                    line=dict(width=1.2, color=field_colors.get(field)),
                    stackgroup="balance_sheet",
                )
            )
    else:
        fig = px.line(df, x=x_field, y=y_field, template="plotly_white", render_mode="svg")
        fig.update_traces(
            mode=LINE_TRACE_MODE,
            connectgaps=True,
            line=dict(width=1.8, color="#5b6cff"),
        )

    return fig, chart_type, title


def build_chart_figure(df, indicator, compact: bool = False) -> go.Figure:
    fig, chart_type, title = _build_base_figure(df, indicator)
    chart_cfg = indicator.chart or {}
    x_field = chart_cfg.get("x_field", "date")
    y_field = chart_cfg.get("y_field", "value")
    axis_color = "#334155" if compact else "#1f2937"
    axis_line_color = "#94a3b8" if compact else "#64748b"
    labels = {
        x_field: chart_cfg.get("x_label", ""),
        y_field: chart_cfg.get("y_label", y_field),
    }

    if compact:
        layout_kwargs = {
            "height": 255,
            "width": 420,
            "margin": dict(l=42, r=18, t=26, b=28),
            "title_font_size": 12,
            "font_size": 12,
        }
    else:
        layout_kwargs = {
            "height": INTERACTIVE_CHART_HEIGHT,
            "width": INTERACTIVE_CHART_WIDTH,
            "margin": dict(l=70, r=40, t=50, b=110),
            "title_font_size": 24,
            "font_size": 18,
        }

    fig.update_layout(
        title=dict(
            text="",
            x=0.02,
            xanchor="left",
            y=0.98,
            yanchor="top",
            font=dict(size=layout_kwargs["title_font_size"]),
        ),
        font=dict(size=layout_kwargs["font_size"], color="#334155"),
        height=layout_kwargs["height"],
        width=layout_kwargs["width"],
        margin=layout_kwargs["margin"],
        paper_bgcolor="white",
        plot_bgcolor="white",
        autosize=False,
        showlegend=chart_type in {"term_structure", "multi_axis_line", "stacked_area"},
    )

    time_tick_count = _estimate_time_tick_count(df, x_field, compact)
    time_tickformat = _estimate_time_tickformat(df, x_field)
    fig.update_xaxes(
        title=dict(text=labels.get(x_field, ""), font=dict(size=19 if not compact else 12, color=axis_color)),
        tickfont=dict(size=17 if not compact else 12, color=axis_color),
        showgrid=True,
        gridcolor="#e6edf7",
        zeroline=False,
        automargin=not compact,
        ticklabelposition="outside",
        showline=True,
        linecolor=axis_line_color,
        tickcolor=axis_line_color,
        tickmode="auto",
        nticks=time_tick_count,
        tickformat=time_tickformat,
    )
    fig.update_yaxes(
        title=dict(text=labels[y_field], font=dict(size=19 if not compact else 12, color=axis_color)),
        tickfont=dict(size=17 if not compact else 12, color=axis_color),
        showgrid=True,
        gridcolor="#e6edf7",
        zeroline=False,
        automargin=not compact,
        ticklabelposition="outside",
        showline=True,
        linecolor=axis_line_color,
        tickcolor=axis_line_color,
    )
    if chart_type == "multi_axis_line":
        fig.update_layout(
            legend=dict(
                orientation="h",
                yanchor="bottom" if compact else "top",
                y=1.02 if compact else 1.03,
                xanchor="left",
                x=0.02 if compact else 0.16,
                font=dict(size=12 if compact else 19, color=axis_color),
                title_text="",
            ),
            hovermode="x unified",
            yaxis=dict(
                title=dict(
                    text=chart_cfg.get("left_y_label", labels.get(y_field, "")),
                    font=dict(size=12 if compact else 19, color=axis_color),
                ),
                showgrid=True,
                gridcolor="#e6edf7",
                tickfont=dict(size=12 if compact else 17, color=axis_color),
                zeroline=False,
                showline=True,
                linecolor=axis_line_color,
                tickcolor=axis_line_color,
            ),
            yaxis2=dict(
                title=dict(
                    text=chart_cfg.get("right_y_label", ""),
                    font=dict(size=12 if compact else 19, color=axis_color),
                ),
                overlaying="y",
                side="right",
                showgrid=False,
                tickfont=dict(size=12 if compact else 17, color=axis_color),
                zeroline=False,
                showline=True,
                linecolor=axis_line_color,
                tickcolor=axis_line_color,
            ),
        )
    if chart_type == "stacked_area":
        fig.update_layout(
            legend=dict(
                orientation="h",
                yanchor="bottom" if compact else "top",
                y=1.02 if compact else 1.03,
                xanchor="left",
                x=0.02,
                font=dict(size=12 if compact else 18, color=axis_color),
                title_text="",
            ),
            hovermode="x unified",
        )
    source_note = chart_cfg.get("source_label") or display_source_label(indicator.source)
    fig.add_annotation(
        text=f"Source: {source_note}",
        xref="paper",
        yref="paper",
        x=0.995,
        y=0.01,
        showarrow=False,
        font=dict(size=12 if compact else 14, color="rgba(60,60,60,0.8)"),
        align="right",
        xanchor="right",
        yanchor="bottom",
        bgcolor="rgba(255,255,255,0.7)",
        borderpad=1,
    )
    if chart_type == "term_structure":
        fig.update_layout(
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="left",
                x=0.02,
                font=dict(size=12 if compact else 18, color=axis_color),
                title_text="",
            ),
            hovermode="x unified",
        )
        fig.update_xaxes(type="category", tickangle=-30)

    if chart_type != "term_structure" and x_field in df.columns and x_field.lower() in {"date", "obs_time", "time"}:
        fig.update_xaxes(hoverformat="%Y-%m-%d")
        fig.update_traces(
            xhoverformat="%Y-%m-%d",
            hovertemplate="%{x|%Y-%m-%d}<br>%{fullData.name}: %{y}<extra></extra>",
        )
    return fig


def render_chart(df, indicator, output_stub: Path, write_image: bool = True) -> Tuple[Path, Path]:
    usable, reason = chart_data_quality(indicator, plot_df=df)
    if not usable:
        raise ValueError(f"Chart rendering blocked by data-quality policy: {reason}")
    output_stub.parent.mkdir(exist_ok=True)
    html_path = output_stub.with_suffix(".html")
    image_path = output_stub.with_suffix(".png")
    compact_fig = build_chart_figure(df, indicator, compact=True)
    html_fig = go.Figure(copy.deepcopy(build_chart_figure(df, indicator, compact=False)))
    figure_json = json.dumps(json.loads(plotly.io.to_json(html_fig)))
    generated_at = datetime.now().isoformat(timespec="seconds")
    plot_config = json.dumps(
        {
            "displayModeBar": True,
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": True,
        }
    )
    html_path.write_text(
        (
            "<!DOCTYPE html>\n"
            "<html lang=\"zh\">\n"
            "<head>\n"
            "  <meta charset=\"utf-8\" />\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
            f"  <meta name=\"generated-at\" content=\"{generated_at}\" />\n"
            "  <title>Interactive Chart</title>\n"
            "  <script charset=\"utf-8\" src=\"https://cdn.plot.ly/plotly-3.4.0.min.js\"></script>\n"
            "  <style>\n"
            "    html, body { width: 100%; height: 100%; margin: 0; background: #ffffff; }\n"
            "    body { overflow: hidden; display: flex; align-items: flex-start; justify-content: center; }\n"
            f"    #chart-root {{ width: min(100vw, calc(100vh * {INTERACTIVE_CHART_WIDTH} / {INTERACTIVE_CHART_HEIGHT})); aspect-ratio: {INTERACTIVE_CHART_ASPECT}; visibility: hidden; }}\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            f"  <!-- generated-at: {generated_at} -->\n"
            "  <div id=\"chart-root\"></div>\n"
            "  <script>\n"
            f"    const figure = {figure_json};\n"
            f"    const config = {plot_config};\n"
            "    const chartRoot = document.getElementById('chart-root');\n"
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
            "    function renderChart() {\n"
            "      if (!window.Plotly || !chartRoot) return;\n"
            "      figure.layout = figure.layout || {};\n"
            "      figure.layout.autosize = false;\n"
            "      figure.layout.width = chartRoot.clientWidth;\n"
            "      figure.layout.height = chartRoot.clientHeight;\n"
            "      applyResponsiveTimeAxis();\n"
            "      Plotly.react(chartRoot, figure.data, figure.layout, config).then(function () {\n"
            "        chartRoot.style.visibility = 'visible';\n"
            "      });\n"
            "    }\n"
            "    renderChart();\n"
            "    window.addEventListener('load', renderChart);\n"
            "    window.addEventListener('resize', function () {\n"
            "      renderChart();\n"
            "    });\n"
            "  </script>\n"
            "</body>\n"
            "</html>\n"
        ),
        encoding="utf-8",
    )
    if write_image:
        _write_compact_image(compact_fig, image_path)
    return html_path, image_path

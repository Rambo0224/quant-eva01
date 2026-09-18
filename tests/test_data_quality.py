import pandas as pd

from macro_replay.config import Indicator
from macro_replay.data_quality import chart_data_quality


def _indicator(metadata=None):
    return Indicator(
        id="test",
        title="test",
        description="",
        server="",
        tool="",
        arguments={},
        chart={"type": "line"},
        theme="test",
        metadata=metadata or {},
    )


def test_proxy_indicator_cannot_render_chart():
    ok, reason = chart_data_quality(
        _indicator({"data_quality": "proxy", "quality_reason": "proxy data"}),
        plot_df=pd.DataFrame({"date": [pd.Timestamp("2026-07-17")], "value": [1.0]}),
    )
    assert not ok
    assert reason == "proxy data"


def test_proxy_marker_in_observation_extra_cannot_render_chart():
    raw_df = pd.DataFrame(
        {
            "extra": [{"note": "1-month return as approximately 20 trading days"}],
        }
    )
    ok, reason = chart_data_quality(
        _indicator(),
        raw_df=raw_df,
        plot_df=pd.DataFrame({"date": [pd.Timestamp("2026-07-17")], "value": [1.0]}),
    )
    assert not ok
    assert "代理" in reason or "近似" in reason

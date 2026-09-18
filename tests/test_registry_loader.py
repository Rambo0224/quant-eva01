from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from macro_replay.config import Indicator, Theme

from datahub.registry import loader


class RegistryLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        loader.clear_registry_caches()

    def tearDown(self) -> None:
        loader.clear_registry_caches()

    def test_loader_uses_explicit_view_and_workspace_configs_when_present(self) -> None:
        indicator = Indicator(
            id="usd-liquidity-dxy",
            title="美元流动性",
            description="DXY overlay",
            server="",
            tool="fred",
            arguments={},
            chart={"type": "line", "y_field": "value", "x_field": "date"},
            theme="usd-liquidity",
            source="fred",
            series=[{"code": "DXY"}],
        )
        theme = Theme(key="usd-liquidity", title="美元流动性", description="")

        with tempfile.TemporaryDirectory() as tmpdir:
            views_dir = Path(tmpdir) / "views"
            workspaces_dir = Path(tmpdir) / "workspaces"
            views_dir.mkdir()
            workspaces_dir.mkdir()
            (views_dir / "views.yaml").write_text(
                """
views:
  - id: usd-liquidity-dxy.default
    dataset_id: usd-liquidity-dxy
    title: 美元流动性主图
    description: 显式覆盖默认视图
  - id: usd-liquidity-dxy.rebased
    dataset_id: usd-liquidity-dxy
    workspace_id: usd-liquidity
    title: 美元流动性比较图
    description: 用于对比分析
    chart_type: line
    x_field: date
    y_field: value
    supports_compare: true
""",
                encoding="utf-8",
            )
            (workspaces_dir / "workspaces.yaml").write_text(
                """
workspaces:
  - id: usd-liquidity
    title: 美元流动性工作台
    description: 自定义工作区
    headline: 先看美元，再看信用
    research_questions:
      - 美元方向有没有变化？
    dataset_ids:
      - usd-liquidity-dxy
    view_ids:
      - usd-liquidity-dxy.default
      - usd-liquidity-dxy.rebased
""",
                encoding="utf-8",
            )

            with patch.object(loader, "VIEWS_DIR", views_dir), patch.object(loader, "WORKSPACES_DIR", workspaces_dir):
                with patch.object(loader, "load_indicators", return_value=[indicator]), patch.object(loader, "load_themes", return_value=[theme]):
                    views = loader.load_view_definitions()
                    workspaces = loader.load_workspace_definitions()

        self.assertEqual({view.id for view in views}, {"usd-liquidity-dxy.default", "usd-liquidity-dxy.rebased"})
        default_view = next(view for view in views if view.id == "usd-liquidity-dxy.default")
        self.assertEqual(default_view.title, "美元流动性主图")
        self.assertEqual(default_view.description, "显式覆盖默认视图")

        self.assertEqual(len(workspaces), 1)
        self.assertEqual(workspaces[0].title, "美元流动性工作台")
        self.assertEqual(workspaces[0].view_ids, ["usd-liquidity-dxy.default", "usd-liquidity-dxy.rebased"])
        self.assertEqual(workspaces[0].headline, "先看美元，再看信用")
        self.assertEqual(workspaces[0].research_questions, ["美元方向有没有变化？"])

    def test_loader_falls_back_to_theme_derived_workspace_when_no_config_exists(self) -> None:
        indicator = Indicator(
            id="credit-hy-spread",
            title="HY Spread",
            description="High yield spread",
            server="",
            tool="fred",
            arguments={},
            chart={"type": "line", "y_field": "spread"},
            theme="credit",
            source="fred",
            series=[{"code": "BAMLH0A0HYM2"}],
        )
        theme = Theme(key="credit", title="信用风险", description="")

        with tempfile.TemporaryDirectory() as tmpdir:
            missing_views_dir = Path(tmpdir) / "views"
            missing_workspaces_dir = Path(tmpdir) / "workspaces"
            with patch.object(loader, "VIEWS_DIR", missing_views_dir), patch.object(loader, "WORKSPACES_DIR", missing_workspaces_dir):
                with patch.object(loader, "load_indicators", return_value=[indicator]), patch.object(loader, "load_themes", return_value=[theme]):
                    views = loader.load_view_definitions()
                    workspaces = loader.load_workspace_definitions()

        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].id, "credit-hy-spread.default")
        self.assertEqual(len(workspaces), 1)
        self.assertEqual(workspaces[0].id, "credit")
        self.assertEqual(workspaces[0].dataset_ids, ["credit-hy-spread"])


if __name__ == "__main__":
    unittest.main()

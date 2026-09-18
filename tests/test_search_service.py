from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import search_service


class SearchServiceTests(unittest.TestCase):
    def test_search_registry_matches_dataset_view_and_workspace(self) -> None:
        datasets = [
            {
                "id": "usd-liquidity-dxy",
                "title": "美元流动性",
                "description": "DXY overlay",
                "workspace_id": "usd-liquidity",
            }
        ]
        views = [
            {
                "id": "usd-liquidity-dxy.default",
                "title": "美元流动性主图",
                "dataset_id": "usd-liquidity-dxy",
                "workspace_id": "usd-liquidity",
                "description": "默认图",
                "chart_type": "line",
            }
        ]
        workspaces = [
            {
                "id": "usd-liquidity",
                "title": "美元流动性工作台",
                "description": "研究美元流动性",
                "dataset_ids": ["usd-liquidity-dxy"],
                "view_ids": ["usd-liquidity-dxy.default"],
            }
        ]

        with patch.object(search_service, "list_datasets", side_effect=[datasets, datasets]), patch.object(
            search_service, "list_views", return_value=views
        ), patch.object(search_service, "list_workspaces", return_value=workspaces):
            results = search_service.search_registry("流动性", workspace_id="usd-liquidity")

        self.assertEqual(results["total"], 3)
        self.assertEqual(results["datasets"][0]["id"], "usd-liquidity-dxy")
        self.assertEqual(results["views"][0]["dataset_title"], "美元流动性")
        self.assertEqual(results["workspaces"][0]["title"], "美元流动性工作台")

    def test_search_registry_filters_non_matching_workspace_results(self) -> None:
        datasets = []
        views = []
        workspaces = [
            {
                "id": "usd-liquidity",
                "title": "美元流动性工作台",
                "description": "",
                "dataset_ids": [],
                "view_ids": [],
            },
            {
                "id": "credit",
                "title": "信用风险工作台",
                "description": "",
                "dataset_ids": [],
                "view_ids": [],
            },
        ]

        with patch.object(search_service, "list_datasets", side_effect=[datasets, datasets]), patch.object(
            search_service, "list_views", return_value=views
        ), patch.object(search_service, "list_workspaces", return_value=workspaces):
            results = search_service.search_registry(query="", workspace_id="credit")

        self.assertEqual([workspace["id"] for workspace in results["workspaces"]], ["credit"])


if __name__ == "__main__":
    unittest.main()

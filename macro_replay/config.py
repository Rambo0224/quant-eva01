from __future__ import annotations

import tomllib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

CONFIG_DIR = Path("config")
MCP_SERVERS_FILE = CONFIG_DIR / "mcp_servers.toml"
INDICATORS_FILE = CONFIG_DIR / "indicators.yaml"
MODULES_DIR = CONFIG_DIR / "modules"
THEME_SETTINGS_FILE = CONFIG_DIR / "theme_settings.toml"


@dataclass
class MCPServer:
    name: str
    url: str
    auth_token: str


@dataclass
class Indicator:
    id: str
    title: str
    description: str
    server: str
    tool: str
    arguments: Dict[str, Any]
    chart: Dict[str, Any]
    theme: str
    source: str = "mcp"
    series: List[Dict[str, Any]] | None = None
    calculation: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Theme:
    key: str
    title: str
    description: str


def _default_theme_title(key: str) -> str:
    custom = {
        "usd-liquidity": "流动性面板",
        "commodity-review": "有色金属数据",
    }
    if key in custom:
        return custom[key]
    return key.replace("-", " ").title()


def _load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    file_path = path or INDICATORS_FILE
    if not file_path.exists():
        raise FileNotFoundError(
            f"Indicator config not found at {file_path}. Copy config/indicators.example.yaml first."
        )

    raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    catalog = {"themes": dict(raw.get("themes") or {})}

    if MODULES_DIR.exists():
        for module_file in sorted(MODULES_DIR.glob("*.yaml")):
            module_raw = yaml.safe_load(module_file.read_text(encoding="utf-8")) or {}
            for key, value in (module_raw.get("themes") or {}).items():
                catalog["themes"][key] = value
    return catalog


def load_mcp_servers(path: Optional[Path] = None) -> Dict[str, MCPServer]:
    configured_path = os.environ.get("OMNISIGNAL_MCP_CONFIG", "").strip()
    file_path = path or (Path(configured_path) if configured_path else MCP_SERVERS_FILE)
    if not file_path.exists():
        raise FileNotFoundError(
            f"MCP server config not found at {file_path}. Copy config/mcp_servers.example.toml first."
        )
    data = tomllib.loads(file_path.read_text(encoding="utf-8"))
    servers: Dict[str, MCPServer] = {}
    for name, props in data.items():
        servers[name] = MCPServer(name=name, url=props["url"], auth_token=props["auth_token"])
    return servers


def load_indicators(path: Optional[Path] = None) -> List[Indicator]:
    raw = _load_catalog(path)
    indicators: List[Indicator] = []
    for theme, theme_data in (raw.get("themes") or {}).items():
        for entry in theme_data.get("indicators", []):
            indicators.append(
                Indicator(
                    id=entry["id"],
                    title=entry.get("title", entry["id"]),
                    description=entry.get("description", ""),
                    server=entry.get("server", ""),
                    tool=entry.get("tool", entry.get("source", "mcp")),
                    arguments=entry.get("arguments", {}),
                    chart=entry.get("chart", {}),
                    theme=theme,
                    source=entry.get("source", entry.get("tool", "mcp")),
                    series=entry.get("series"),
                    calculation=entry.get("calculation"),
                    metadata=dict(entry.get("metadata") or {}),
                )
            )
    return indicators


def load_themes(path: Optional[Path] = None) -> List[Theme]:
    raw = _load_catalog(path)
    themes: List[Theme] = []
    for key, theme_data in (raw.get("themes") or {}).items():
        themes.append(
            Theme(
                key=key,
                title=theme_data.get("title", _default_theme_title(key)),
                description=theme_data.get("description", ""),
            )
        )
    return themes


def _load_settings_file() -> Dict[str, Any]:
    if not THEME_SETTINGS_FILE.exists():
        return {}
    return tomllib.loads(THEME_SETTINGS_FILE.read_text(encoding="utf-8"))


def _save_settings_file(settings: Dict[str, Any]) -> None:
    import tomli_w

    THEME_SETTINGS_FILE.write_text(tomli_w.dumps(settings), encoding="utf-8")


def load_app_settings() -> Dict[str, str]:
    data = _load_settings_file()
    return dict(data.get("app", {}))


def save_app_settings(settings: Dict[str, str]) -> None:
    data = _load_settings_file()
    data["app"] = settings
    _save_settings_file(data)


def load_theme_settings() -> Dict[str, Dict[str, str]]:
    data = _load_settings_file()
    return {key: dict(value) for key, value in data.get("themes", {}).items()}


def save_theme_settings(settings: Dict[str, Dict[str, str]]) -> None:
    data = _load_settings_file()
    data["themes"] = settings
    _save_settings_file(data)


def find_indicator(indicators: List[Indicator], indicator_id: str) -> Indicator:
    for indicator in indicators:
        if indicator.id == indicator_id:
            return indicator
    raise KeyError(f"Indicator '{indicator_id}' not found in config")

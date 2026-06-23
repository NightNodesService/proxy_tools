from __future__ import annotations

import json
import sys
from pathlib import Path

from .models import AppSettings, TargetSite


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return app_root()


APP_ROOT = app_root()
CONFIG_DIR = APP_ROOT / "config"
BUNDLED_CONFIG_DIR = bundled_root() / "config"
TARGETS_PATH = CONFIG_DIR / "targets.json"
BUNDLED_TARGETS_PATH = BUNDLED_CONFIG_DIR / "targets.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"


def load_targets(path: Path = TARGETS_PATH) -> list[TargetSite]:
    source_path = path if path.exists() else BUNDLED_TARGETS_PATH
    if not source_path.exists():
        return []

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    return [
        TargetSite(
            name=item["name"],
            url=item["url"],
            category=item.get("category", "General"),
            enabled=bool(item.get("enabled", True)),
        )
        for item in payload.get("targets", [])
    ]


def save_targets(targets: list[TargetSite], path: Path = TARGETS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "targets": [
            {
                "name": target.name,
                "url": target.url,
                "category": target.category,
                "enabled": target.enabled,
            }
            for target in targets
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_settings(path: Path = SETTINGS_PATH) -> AppSettings:
    if not path.exists():
        return AppSettings()

    payload = json.loads(path.read_text(encoding="utf-8"))
    return AppSettings(
        timeout_seconds=float(payload.get("timeout_seconds", 12.0)),
        language=str(payload.get("language", "zh")),
        theme=str(payload.get("theme", "tech_dark")),
        local_chrome_test=bool(payload.get("local_chrome_test", False)),
        ipinfo_token=str(payload.get("ipinfo_token", "")),
        proxycheck_key=str(payload.get("proxycheck_key", "")),
        abuseipdb_key=str(payload.get("abuseipdb_key", "")),
        ipqualityscore_key=str(payload.get("ipqualityscore_key", "")),
        user_agent=str(payload.get("user_agent", AppSettings().user_agent)),
    )


def save_settings(settings: AppSettings, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timeout_seconds": settings.timeout_seconds,
        "language": settings.language,
        "theme": settings.theme,
        "local_chrome_test": settings.local_chrome_test,
        "ipinfo_token": settings.ipinfo_token,
        "proxycheck_key": settings.proxycheck_key,
        "abuseipdb_key": settings.abuseipdb_key,
        "ipqualityscore_key": settings.ipqualityscore_key,
        "user_agent": settings.user_agent,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

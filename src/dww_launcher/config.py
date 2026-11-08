"""Persistent settings stored as JSON in the OS-appropriate config directory."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "dww_launcher"


def config_path() -> Path:
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "settings.json"


def default_demo_dir() -> str:
    return str(Path(user_data_dir(APP_NAME, appauthor=False)) / "demos")


@dataclass
class Settings:
    ports: list[str] = field(default_factory=list)
    iwads: list[str] = field(default_factory=list)
    demo_dir: str = field(default_factory=default_demo_dir)
    last_port: str = ""
    last_iwad: str = ""
    last_pwads: list[str] = field(default_factory=list)
    last_extra_args: str = ""
    auto_sequence: bool = True
    keep_latest_attempt: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

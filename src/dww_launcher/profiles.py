"""Configuration profiles: named snapshots of every launcher setting (except auto-download)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from dww_launcher.config import config_path

PROFILE_FORMAT = 1


@dataclass
class Profile:
    # Game
    port: str = ""
    iwad: str = ""
    pwads: list[str] = field(default_factory=list)
    map: str | None = None
    skill: int | None = 4
    complevel: int | None = None
    fast: bool = False
    respawn: bool = False
    nomonsters: bool = False
    tas: bool = False
    dsdademo: bool = False
    extra_args: str = ""
    # Demo recording / playback
    record: bool = False
    record_name: str | None = None  # None = automatic name
    auto_sequence: bool = True
    keep_latest_attempt: bool = False
    demo_dir: str = ""
    timedemo: bool = False
    skipsec: str = ""
    # Demo submission
    txt_dir: str = ""
    txt_dir_same_as_demo: bool = True

    def to_dict(self) -> dict:
        return {"format": PROFILE_FORMAT, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def profiles_dir() -> Path:
    return config_path().parent / "profiles"


def _filename(name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".") or "profile"
    return f"{safe}.json"


def list_profiles(directory: Path | None = None) -> list[str]:
    directory = directory or profiles_dir()
    names = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            names.append(json.loads(path.read_text(encoding="utf-8")).get("name") or path.stem)
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(names, key=str.casefold)


def _path_for(name: str, directory: Path) -> Path:
    """The file holding profile ``name`` (an existing file with that stored name wins)."""
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("name") == name:
                    return path
            except (OSError, ValueError, AttributeError):
                continue
    return directory / _filename(name)


def profile_exists(name: str, directory: Path | None = None) -> bool:
    return _path_for(name, directory or profiles_dir()).is_file()


def save_profile(name: str, profile: Profile, directory: Path | None = None) -> Path:
    directory = directory or profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _path_for(name, directory)
    data = {"name": name, **profile.to_dict()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_profile(name: str, directory: Path | None = None) -> Profile:
    """Raises OSError / ValueError if the profile can't be read."""
    path = _path_for(name, directory or profiles_dir())
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a profile")
    return Profile.from_dict(data)


def delete_profile(name: str, directory: Path | None = None) -> None:
    _path_for(name, directory or profiles_dir()).unlink(missing_ok=True)

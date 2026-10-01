"""Immutable image fixtures live outside the writable database volume."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resource_path(name: str) -> Path:
    packaged = PROJECT_ROOT / "resources" / name
    return packaged if packaged.exists() else PROJECT_ROOT / "data" / name

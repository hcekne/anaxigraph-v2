"""SQLite implementation of the small knowledge-store contract."""

from .parts import read_parts
from .store import Store

__all__ = ["Store", "read_parts"]

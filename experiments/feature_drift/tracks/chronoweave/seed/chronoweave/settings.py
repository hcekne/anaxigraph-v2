"""Process configuration from the environment."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    external_url: str
    data_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            external_url=os.environ.get("CHRONOWEAVE_EXTERNAL_URL", "http://127.0.0.1:8765").rstrip(
                "/"
            ),
            data_dir=Path(os.environ.get("CHRONOWEAVE_DATA_DIR", "data")),
        )

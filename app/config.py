from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str
    session_secret: str
    admin_username: str
    admin_password: str
    release_product: str
    release_output_root: Path
    release_private_key: Path


def get_settings() -> Settings:
    workspace_dir = Path("/home/jpuchky/.openclaw/workspace").expanduser()
    app_root = Path(
        os.getenv("HADS_UPDATER_APP_ROOT", str(workspace_dir / "hads-updater"))
    ).expanduser()

    return Settings(
        app_name=os.getenv("HADS_UPDATER_APP_NAME", "HADS Updater"),
        session_secret=os.getenv(
            "HADS_RELEASE_SESSION_SECRET", "change-me-before-production"
        ),
        admin_username=os.getenv("HADS_RELEASE_ADMIN_USERNAME", "admin"),
        admin_password=os.getenv("HADS_RELEASE_ADMIN_PASSWORD", "admin"),
        release_product=os.getenv("HADS_RELEASE_PRODUCT", "HADS"),
        release_output_root=Path(
            os.getenv("HADS_RELEASE_OUTPUT_ROOT", str(app_root / "releases"))
        ).expanduser().resolve(),
        release_private_key=Path(
            os.getenv("HADS_RELEASE_PRIVATE_KEY", str(app_root / "keys" / "hads.pem"))
        ).expanduser().resolve(),
    )

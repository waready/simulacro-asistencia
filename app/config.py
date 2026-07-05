from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional during static validation
    def load_dotenv() -> bool:
        return False

load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_version: str
    storage_backend: str
    data_json_path: Path
    excel_path: Path | None
    results_excel_path: Path | None
    api_key: str | None
    auto_rebuild_data: bool
    default_limit: int
    max_limit: int
    database_url: str | None
    db_host: str | None
    db_port: int
    db_name: str | None
    db_user: str | None
    db_password: str | None
    db_charset: str
    db_students_table: str
    db_event_table: str
    db_results_table: str
    db_auto_seed: bool
    mysql_cache_ttl_seconds: int
    cors_origins: tuple[str, ...]
    cors_methods: tuple[str, ...]
    cors_headers: tuple[str, ...]
    cors_allow_credentials: bool


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_csv(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    items = [item.strip() for item in value.split(",")]
    return tuple(item for item in items if item)


def _parse_database_url(database_url: str | None) -> dict[str, str | int | None]:
    if not database_url:
        return {}

    parsed = urlparse(database_url)
    if parsed.scheme.lower() not in {"mysql", "mysql+pymysql"}:
        raise ValueError("SIMULACRO_DATABASE_URL must use mysql:// or mysql+pymysql://.")

    return {
        "db_host": parsed.hostname,
        "db_port": parsed.port or 3306,
        "db_name": parsed.path.lstrip("/") or None,
        "db_user": unquote(parsed.username) if parsed.username else None,
        "db_password": unquote(parsed.password) if parsed.password else None,
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root_dir = Path(__file__).resolve().parent.parent
    data_json = os.getenv("SIMULACRO_DATA_JSON", str(root_dir / "data" / "simulacro_dataset.json"))
    excel_path = os.getenv("SIMULACRO_EXCEL_PATH")
    results_excel_path = os.getenv("SIMULACRO_RESULTS_EXCEL_PATH")
    database_url = os.getenv("SIMULACRO_DATABASE_URL")
    database_parts = _parse_database_url(database_url)

    return Settings(
        app_name=os.getenv("SIMULACRO_APP_NAME", "Simulacro API"),
        app_version=os.getenv("SIMULACRO_APP_VERSION", "1.0.0"),
        storage_backend=os.getenv("SIMULACRO_STORAGE_BACKEND", "json").strip().lower(),
        data_json_path=Path(data_json).expanduser().resolve(),
        excel_path=Path(excel_path).expanduser().resolve() if excel_path else None,
        results_excel_path=Path(results_excel_path).expanduser().resolve() if results_excel_path else None,
        api_key=os.getenv("SIMULACRO_API_KEY"),
        auto_rebuild_data=_to_bool(os.getenv("SIMULACRO_AUTO_REBUILD_DATA"), default=True),
        default_limit=int(os.getenv("SIMULACRO_DEFAULT_LIMIT", "20")),
        max_limit=int(os.getenv("SIMULACRO_MAX_LIMIT", "100")),
        database_url=database_url,
        db_host=(os.getenv("SIMULACRO_DB_HOST") or database_parts.get("db_host")),
        db_port=int(os.getenv("SIMULACRO_DB_PORT", str(database_parts.get("db_port", 3306)))),
        db_name=(os.getenv("SIMULACRO_DB_NAME") or database_parts.get("db_name")),
        db_user=(os.getenv("SIMULACRO_DB_USER") or database_parts.get("db_user")),
        db_password=(os.getenv("SIMULACRO_DB_PASSWORD") or database_parts.get("db_password")),
        db_charset=os.getenv("SIMULACRO_DB_CHARSET", "utf8mb4"),
        db_students_table=os.getenv("SIMULACRO_DB_STUDENTS_TABLE", "simulacro_alumnos"),
        db_event_table=os.getenv("SIMULACRO_DB_EVENT_TABLE", "simulacro_evento"),
        db_results_table=os.getenv("SIMULACRO_DB_RESULTS_TABLE", "simulacro_resultados"),
        db_auto_seed=_to_bool(os.getenv("SIMULACRO_DB_AUTO_SEED"), default=False),
        mysql_cache_ttl_seconds=int(os.getenv("SIMULACRO_MYSQL_CACHE_TTL_SECONDS", "60")),
        cors_origins=_to_csv(os.getenv("SIMULACRO_CORS_ORIGINS"), default=()),
        cors_methods=_to_csv(os.getenv("SIMULACRO_CORS_METHODS"), default=("GET", "POST", "PUT", "DELETE", "OPTIONS")),
        cors_headers=_to_csv(os.getenv("SIMULACRO_CORS_HEADERS"), default=("Authorization", "Content-Type", "X-API-Key")),
        cors_allow_credentials=_to_bool(os.getenv("SIMULACRO_CORS_ALLOW_CREDENTIALS"), default=False),
    )

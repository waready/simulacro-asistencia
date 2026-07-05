from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.importer import build_dataset
from app.mysql_storage import load_dataset_to_mysql, write_mysql_dump


def cli() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Load the simulacro Excel dataset into MySQL or export a phpMyAdmin-friendly SQL file."
    )
    parser.add_argument("--excel", required=True, help="Path to the source .xlsx file.")
    parser.add_argument("--results-excel", help="Optional path to the results .xlsx file.")
    parser.add_argument(
        "--mode",
        choices=["mysql", "sql"],
        default="mysql",
        help="`mysql` inserts directly into the configured database. `sql` creates a .sql dump file.",
    )
    parser.add_argument(
        "--out",
        help="Destination .sql file when mode=sql. Defaults to ./data/simulacro_aulas_dump.sql",
    )
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Do not clear existing rows before importing.",
    )
    args = parser.parse_args()

    excel_path = Path(args.excel).expanduser().resolve()
    results_excel_path = Path(args.results_excel).expanduser().resolve() if args.results_excel else settings.results_excel_path
    dataset = build_dataset(excel_path, results_excel_path=results_excel_path)
    truncate = not args.no_truncate

    if args.mode == "sql":
        output_path = (
            Path(args.out).expanduser().resolve()
            if args.out
            else (Path.cwd() / "data" / "simulacro_aulas_dump.sql").resolve()
        )
        dump_path = write_mysql_dump(dataset, settings, output_path, truncate=truncate)
        print(json.dumps({"mode": "sql", "output": str(dump_path)}, ensure_ascii=False, indent=2))
        return 0

    result = load_dataset_to_mysql(dataset, settings, truncate=truncate)
    print(json.dumps({"mode": "mysql", "results_excel": str(results_excel_path) if results_excel_path else "", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())

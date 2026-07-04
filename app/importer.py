from __future__ import annotations

import argparse
import json
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"main": MAIN_NS}


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    without_dash = without_marks.replace("\u2014", "-")
    return " ".join(without_dash.upper().split())


def split_block_header(value: str) -> dict[str, Any] | None:
    parts = [segment.strip() for segment in re.split(r"\s+[—-]\s+", value) if segment.strip()]
    if len(parts) != 3:
        return None

    parts_normalized = [normalize_text(part) for part in parts]
    if not parts_normalized[0].startswith("AREA:"):
        return None
    if not parts_normalized[1].startswith("SALON "):
        return None
    if "ESTUDIANTES" not in parts_normalized[2]:
        return None

    capacity_match = re.search(r"(\d+)", parts_normalized[2])
    if capacity_match is None:
        return None

    area = parts[0].split(":", 1)[1].strip() if ":" in parts[0] else parts[0].strip()
    salon = parts[1].split(" ", 1)[1].strip() if " " in parts[1] else parts[1].strip()

    return {
        "area": area,
        "salon": salon,
        "capacidad_salon": int(capacity_match.group(1)),
    }


def is_header_row(row: list[str]) -> bool:
    if len(row) < 4:
        return False
    normalized = [normalize_text(item) for item in row[:4]]
    return (
        normalized[0].startswith("N")
        and normalized[1] == "DNI"
        and normalized[2] == "APELLIDOS Y NOMBRES"
        and normalized[3] == "SEDE"
    )


def column_index(cell_reference: str) -> int:
    column_name = "".join(ch for ch in cell_reference if ch.isalpha())
    result = 0
    for char in column_name:
        result = result * 26 + (ord(char.upper()) - 64)
    return result - 1


def cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iterfind(".//main:t", NS))

    raw_value = cell.find("main:v", NS)
    if raw_value is None:
        return ""

    text = raw_value.text or ""
    if cell_type == "s" and text.isdigit():
        return shared_strings[int(text)]
    return text


def load_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []

    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("main:si", NS):
        values.append("".join(node.text or "" for node in item.iterfind(".//main:t", NS)))
    return values


def iter_rows(excel_path: Path) -> list[list[str]]:
    with zipfile.ZipFile(excel_path) as workbook:
        shared_strings = load_shared_strings(workbook)
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        sheet_data = sheet.find("main:sheetData", NS)
        if sheet_data is None:
            return []

        rows: list[list[str]] = []
        for row in sheet_data.findall("main:row", NS):
            values: dict[int, str] = {}
            max_index = -1
            for cell in row.findall("main:c", NS):
                index = column_index(cell.attrib.get("r", "A1"))
                max_index = max(max_index, index)
                values[index] = cell_value(cell, shared_strings)
            rows.append([values.get(index, "") for index in range(max_index + 1)])
        return rows


def parse_dataset(excel_path: Path) -> dict[str, Any]:
    rows = iter_rows(excel_path)
    if len(rows) < 5:
        raise ValueError(f"The Excel file {excel_path} does not contain the expected layout.")

    institution = rows[0][0].strip() if rows and rows[0] else ""
    organizer = rows[1][0].strip() if len(rows) > 1 and rows[1] else ""
    title = rows[2][0].strip() if len(rows) > 2 and rows[2] else ""

    students: list[dict[str, Any]] = []
    current_block: dict[str, Any] | None = None

    for row_number, row in enumerate(rows, start=1):
        if not row:
            continue

        first_cell = row[0].strip()
        if not first_cell:
            continue

        block_header = split_block_header(first_cell)
        if block_header is not None:
            current_block = block_header
            continue

        if is_header_row(row):
            continue

        if current_block is None or len(row) < 4:
            continue

        order_number = row[0].strip()
        dni = row[1].strip()
        full_name = row[2].strip()
        sede = row[3].strip()

        if not order_number.isdigit() or not dni.isdigit():
            continue

        students.append(
            {
                "numero_orden": int(order_number),
                "dni": dni,
                "nombre": full_name,
                "sede": sede,
                "area": current_block["area"],
                "salon": current_block["salon"],
                "capacidad_salon": current_block["capacidad_salon"],
                "fila_excel": row_number,
                "asistencia": 0,
            }
        )

    if not students:
        raise ValueError(f"No students could be parsed from {excel_path}.")

    areas = sorted({student["area"] for student in students})
    salones = sorted({student["salon"] for student in students})
    sedes = sorted({student["sede"] for student in students})

    by_area = dict(sorted(Counter(student["area"] for student in students).items()))
    by_sede = dict(sorted(Counter(student["sede"] for student in students).items()))

    return {
        "source_file": str(excel_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evento": {
            "institucion": institution,
            "organizador": organizer,
            "titulo": title,
        },
        "resumen": {
            "total_alumnos": len(students),
            "total_areas": len(areas),
            "total_salones": len(salones),
            "total_sedes": len(sedes),
            "areas": areas,
            "sedes": sedes,
            "alumnos_por_area": by_area,
            "alumnos_por_sede": by_sede,
        },
        "alumnos": students,
    }


def write_dataset(dataset: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")


def build_dataset(excel_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    dataset = parse_dataset(excel_path)
    if output_path is not None:
        write_dataset(dataset, output_path)
    return dataset


def cli() -> int:
    parser = argparse.ArgumentParser(description="Build a normalized JSON dataset from the simulacro Excel file.")
    parser.add_argument("--excel", required=True, help="Path to the source .xlsx file.")
    parser.add_argument("--out", help="Path to the generated JSON file.")
    args = parser.parse_args()

    excel_path = Path(args.excel).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve() if args.out else None

    dataset = build_dataset(excel_path, output_path)
    print(
        json.dumps(
            {
                "source_file": dataset["source_file"],
                "total_alumnos": dataset["resumen"]["total_alumnos"],
                "total_salones": dataset["resumen"]["total_salones"],
                "total_sedes": dataset["resumen"]["total_sedes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())

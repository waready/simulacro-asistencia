from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.importer import normalize_text

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def mysql_is_configured(settings: Settings) -> bool:
    return all([settings.db_host, settings.db_name, settings.db_user])


def _require_mysql_client():
    try:
        import pymysql  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "PyMySQL is not installed. Run `pip install -r requirements.txt` to enable the MySQL backend."
        ) from exc
    return pymysql


def validate_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"Invalid MySQL identifier: {identifier!r}")
    return identifier


def quote_identifier(identifier: str) -> str:
    return f"`{validate_identifier(identifier)}`"


def mysql_connection_kwargs(settings: Settings) -> dict[str, Any]:
    if not mysql_is_configured(settings):
        raise RuntimeError(
            "MySQL backend is not fully configured. Set SIMULACRO_DB_HOST, SIMULACRO_DB_NAME, "
            "SIMULACRO_DB_USER and SIMULACRO_DB_PASSWORD or SIMULACRO_DATABASE_URL."
        )

    return {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password or "",
        "database": settings.db_name,
        "charset": settings.db_charset,
        "autocommit": False,
    }


def get_mysql_connection(settings: Settings):
    pymysql = _require_mysql_client()
    return pymysql.connect(
        **mysql_connection_kwargs(settings),
        cursorclass=pymysql.cursors.DictCursor,
    )


def mysql_generated_at(value: str | None) -> str:
    if not value:
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def student_search_text(student: dict[str, Any]) -> str:
    return normalize_text(
        " ".join(
            [
                student["dni"],
                student["nombre"],
                student["sede"],
                student["area"],
                student["salon"],
            ]
        )
    )


def render_mysql_schema(settings: Settings) -> str:
    students_table = quote_identifier(settings.db_students_table)
    event_table = quote_identifier(settings.db_event_table)

    return f"""
CREATE TABLE IF NOT EXISTS {event_table} (
    id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    institucion VARCHAR(255) NOT NULL,
    organizador VARCHAR(255) NOT NULL,
    titulo VARCHAR(255) NOT NULL,
    source_file VARCHAR(1024) NULL,
    generated_at DATETIME NOT NULL,
    total_alumnos INT NOT NULL,
    total_areas INT NOT NULL,
    total_salones INT NOT NULL,
    total_sedes INT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET={settings.db_charset};

CREATE TABLE IF NOT EXISTS {students_table} (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    numero_orden INT NOT NULL,
    dni VARCHAR(16) NOT NULL,
    nombre VARCHAR(255) NOT NULL,
    sede VARCHAR(120) NOT NULL,
    area VARCHAR(120) NOT NULL,
    salon VARCHAR(60) NOT NULL,
    sede_key VARCHAR(120) NOT NULL,
    area_key VARCHAR(120) NOT NULL,
    salon_key VARCHAR(60) NOT NULL,
    capacidad_salon INT NOT NULL,
    fila_excel INT NOT NULL,
    asistencia TINYINT(1) NOT NULL DEFAULT 0,
    search_text VARCHAR(600) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_{validate_identifier(settings.db_students_table)}_dni (dni),
    KEY idx_{validate_identifier(settings.db_students_table)}_area_key (area_key),
    KEY idx_{validate_identifier(settings.db_students_table)}_salon_key (salon_key),
    KEY idx_{validate_identifier(settings.db_students_table)}_sede_key (sede_key)
) ENGINE=InnoDB DEFAULT CHARSET={settings.db_charset};
""".strip()


def ensure_mysql_schema(settings: Settings) -> None:
    schema_sql = render_mysql_schema(settings)
    statements = [statement.strip() for statement in schema_sql.split(";") if statement.strip()]
    with get_mysql_connection(settings) as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            cursor.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                  AND column_name = 'asistencia'
                """,
                (settings.db_name, settings.db_students_table),
            )
            has_asistencia = int(cursor.fetchone()["total"]) > 0
            if not has_asistencia:
                cursor.execute(
                    f"""
                    ALTER TABLE {quote_identifier(settings.db_students_table)}
                    ADD COLUMN asistencia TINYINT(1) NOT NULL DEFAULT 0 AFTER fila_excel
                    """
                )
        connection.commit()


def load_dataset_to_mysql(dataset: dict[str, Any], settings: Settings, *, truncate: bool = True) -> dict[str, int]:
    ensure_mysql_schema(settings)

    students_table = quote_identifier(settings.db_students_table)
    event_table = quote_identifier(settings.db_event_table)
    generated_at = mysql_generated_at(dataset.get("generated_at"))
    summary = dataset["resumen"]
    event = dataset["evento"]
    students = dataset["alumnos"]

    rows = [
        (
            student["numero_orden"],
            student["dni"],
            student["nombre"],
            student["sede"],
            student["area"],
            student["salon"],
            normalize_text(student["sede"]),
            normalize_text(student["area"]),
            normalize_text(student["salon"]),
            student["capacidad_salon"],
            student["fila_excel"],
            int(bool(student.get("asistencia", False))),
            student_search_text(student),
        )
        for student in students
    ]

    with get_mysql_connection(settings) as connection:
        with connection.cursor() as cursor:
            if truncate:
                cursor.execute(f"DELETE FROM {students_table}")
                cursor.execute(f"DELETE FROM {event_table}")

            cursor.execute(
                f"""
                INSERT INTO {event_table} (
                    id, institucion, organizador, titulo, source_file, generated_at,
                    total_alumnos, total_areas, total_salones, total_sedes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    institucion = VALUES(institucion),
                    organizador = VALUES(organizador),
                    titulo = VALUES(titulo),
                    source_file = VALUES(source_file),
                    generated_at = VALUES(generated_at),
                    total_alumnos = VALUES(total_alumnos),
                    total_areas = VALUES(total_areas),
                    total_salones = VALUES(total_salones),
                    total_sedes = VALUES(total_sedes)
                """,
                (
                    1,
                    event["institucion"],
                    event["organizador"],
                    event["titulo"],
                    dataset.get("source_file"),
                    generated_at,
                    summary["total_alumnos"],
                    summary["total_areas"],
                    summary["total_salones"],
                    summary["total_sedes"],
                ),
            )

            cursor.executemany(
                f"""
                INSERT INTO {students_table} (
                    numero_orden, dni, nombre, sede, area, salon,
                    sede_key, area_key, salon_key,
                    capacidad_salon, fila_excel, asistencia, search_text
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    numero_orden = VALUES(numero_orden),
                    nombre = VALUES(nombre),
                    sede = VALUES(sede),
                    area = VALUES(area),
                    salon = VALUES(salon),
                    sede_key = VALUES(sede_key),
                    area_key = VALUES(area_key),
                    salon_key = VALUES(salon_key),
                    capacidad_salon = VALUES(capacidad_salon),
                    fila_excel = VALUES(fila_excel),
                    asistencia = VALUES(asistencia),
                    search_text = VALUES(search_text)
                """,
                rows,
            )

        connection.commit()

    return {
        "total_alumnos": len(rows),
        "total_areas": int(summary["total_areas"]),
        "total_salones": int(summary["total_salones"]),
        "total_sedes": int(summary["total_sedes"]),
    }


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)

    text = str(value)
    text = text.replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


def write_mysql_dump(
    dataset: dict[str, Any],
    settings: Settings,
    output_path: Path,
    *,
    truncate: bool = True,
    batch_size: int = 500,
) -> Path:
    students_table = quote_identifier(settings.db_students_table)
    event_table = quote_identifier(settings.db_event_table)
    generated_at = mysql_generated_at(dataset.get("generated_at"))
    summary = dataset["resumen"]
    event = dataset["evento"]
    students = dataset["alumnos"]

    lines = [render_mysql_schema(settings), ""]
    if truncate:
        lines.extend(
            [
                "SET FOREIGN_KEY_CHECKS = 0;",
                f"DELETE FROM {students_table};",
                f"DELETE FROM {event_table};",
                "SET FOREIGN_KEY_CHECKS = 1;",
                "",
            ]
        )

    event_values = ", ".join(
        [
            sql_literal(1),
            sql_literal(event["institucion"]),
            sql_literal(event["organizador"]),
            sql_literal(event["titulo"]),
            sql_literal(dataset.get("source_file")),
            sql_literal(generated_at),
            sql_literal(summary["total_alumnos"]),
            sql_literal(summary["total_areas"]),
            sql_literal(summary["total_salones"]),
            sql_literal(summary["total_sedes"]),
        ]
    )
    lines.append(
        f"""
INSERT INTO {event_table} (
    id, institucion, organizador, titulo, source_file, generated_at,
    total_alumnos, total_areas, total_salones, total_sedes
) VALUES ({event_values})
ON DUPLICATE KEY UPDATE
    institucion = VALUES(institucion),
    organizador = VALUES(organizador),
    titulo = VALUES(titulo),
    source_file = VALUES(source_file),
    generated_at = VALUES(generated_at),
    total_alumnos = VALUES(total_alumnos),
    total_areas = VALUES(total_areas),
    total_salones = VALUES(total_salones),
    total_sedes = VALUES(total_sedes);
""".strip()
    )
    lines.append("")

    for batch_start in range(0, len(students), batch_size):
        chunk = students[batch_start : batch_start + batch_size]
        values_sql = []
        for student in chunk:
            values_sql.append(
                "("
                + ", ".join(
                    [
                        sql_literal(student["numero_orden"]),
                        sql_literal(student["dni"]),
                        sql_literal(student["nombre"]),
                        sql_literal(student["sede"]),
                        sql_literal(student["area"]),
                        sql_literal(student["salon"]),
                        sql_literal(normalize_text(student["sede"])),
                        sql_literal(normalize_text(student["area"])),
                        sql_literal(normalize_text(student["salon"])),
                        sql_literal(student["capacidad_salon"]),
                        sql_literal(student["fila_excel"]),
                        sql_literal(int(bool(student.get("asistencia", False)))),
                        sql_literal(student_search_text(student)),
                    ]
                )
                + ")"
            )

        joined_values = ",\n".join(values_sql)
        lines.append(
            f"""
INSERT INTO {students_table} (
    numero_orden, dni, nombre, sede, area, salon,
    sede_key, area_key, salon_key,
    capacidad_salon, fila_excel, asistencia, search_text
) VALUES
{joined_values}
ON DUPLICATE KEY UPDATE
    numero_orden = VALUES(numero_orden),
    nombre = VALUES(nombre),
    sede = VALUES(sede),
    area = VALUES(area),
    salon = VALUES(salon),
    sede_key = VALUES(sede_key),
    area_key = VALUES(area_key),
    salon_key = VALUES(salon_key),
    capacidad_salon = VALUES(capacidad_salon),
    fila_excel = VALUES(fila_excel),
    asistencia = VALUES(asistencia),
    search_text = VALUES(search_text);
""".strip()
        )
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Protocol

from app.config import Settings
from app.importer import build_dataset, build_results_summary, normalize_text
from app.mysql_storage import (
    ensure_mysql_schema,
    get_mysql_connection,
    load_dataset_to_mysql,
    mysql_is_configured,
)


class DuplicateStudentError(ValueError):
    """Raised when trying to create a student with an existing DNI."""


@dataclass(slots=True)
class IndexedStudent:
    student: dict[str, Any]
    search_blob: str
    area_key: str
    salon_key: str
    sede_key: str


@dataclass(slots=True)
class IndexedResult:
    result: dict[str, Any]
    search_blob: str
    dependencia_key: str
    estado_key: str


class Repository(Protocol):
    def load(self) -> None: ...

    def summary(self) -> dict[str, Any]: ...

    def results_summary(self) -> dict[str, Any]: ...

    def get_student_by_dni(self, dni: str) -> dict[str, Any] | None: ...

    def get_result_by_dni(self, dni: str) -> dict[str, Any] | None: ...

    def get_public_result_by_dni(self, dni: str) -> dict[str, Any] | None: ...

    def search_students(
        self,
        *,
        dni: str | None = None,
        q: str | None = None,
        area: str | None = None,
        salon: str | None = None,
        sede: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[int, list[dict[str, Any]]]: ...

    def search_results(
        self,
        *,
        dni: str | None = None,
        q: str | None = None,
        dependencia: str | None = None,
        estado_resultado: str | None = None,
        only_zero: bool = False,
        puntaje_min: float | None = None,
        puntaje_max: float | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[int, list[dict[str, Any]]]: ...

    def create_student(self, student: dict[str, Any]) -> dict[str, Any]: ...

    def update_student(self, dni: str, student: dict[str, Any]) -> dict[str, Any] | None: ...

    def delete_student(self, dni: str) -> bool: ...

    def update_attendance(self, dni: str, asistencia: bool) -> dict[str, Any] | None: ...


def build_indexed_student(student: dict[str, Any]) -> IndexedStudent:
    return IndexedStudent(
        student=student,
        search_blob=normalize_text(" ".join([student["dni"], student["nombre"], student["sede"], student["area"], student["salon"]])),
        area_key=normalize_text(student["area"]),
        salon_key=normalize_text(student["salon"]),
        sede_key=normalize_text(student["sede"]),
    )


def normalize_result_record(result: dict[str, Any]) -> dict[str, Any]:
    puntaje_reportado_raw = result.get("puntaje_reportado")
    puntaje_original = str(result.get("puntaje_original", "") or "")
    puntaje_reportado = None if puntaje_reportado_raw in (None, "") else float(puntaje_reportado_raw)
    puntaje = float(result.get("puntaje", 0) or 0)
    respuestas = str(result.get("respuestas", "") or "")

    return {
        "dni": str(result.get("dni", "") or ""),
        "paterno": str(result.get("paterno", "") or ""),
        "materno": str(result.get("materno", "") or ""),
        "nombres": str(result.get("nombres", "") or ""),
        "nombre_completo": str(result.get("nombre_completo", "") or ""),
        "cod_plaza": str(result.get("cod_plaza", "") or ""),
        "plaza": str(result.get("plaza", "") or ""),
        "dependencia": str(result.get("dependencia", "") or ""),
        "aula": str(result.get("aula", "") or ""),
        "litho_ide": str(result.get("litho_ide", "") or ""),
        "lectura_nro_ide": str(result.get("lectura_nro_ide", "") or ""),
        "cod_examen": str(result.get("cod_examen", "") or ""),
        "litho_res": str(result.get("litho_res", "") or ""),
        "lectura_nro_res": str(result.get("lectura_nro_res", "") or ""),
        "respuestas": respuestas,
        "puntaje": puntaje,
        "puntaje_reportado": puntaje_reportado,
        "puntaje_original": puntaje_original,
        "puntaje_fue_completado": bool(result.get("puntaje_fue_completado", puntaje_reportado is not None)),
        "puntaje_es_cero": bool(result.get("puntaje_es_cero", puntaje == 0)),
        "respuestas_vacias": bool(result.get("respuestas_vacias", not "".join(respuestas.split()))),
        "estado_resultado": str(result.get("estado_resultado", "") or ""),
        "fila_excel": int(result.get("fila_excel", 0) or 0),
    }


def build_indexed_result(result: dict[str, Any]) -> IndexedResult:
    return IndexedResult(
        result=result,
        search_blob=normalize_text(
            " ".join(
                [
                    result["dni"],
                    result["paterno"],
                    result["materno"],
                    result["nombres"],
                    result["nombre_completo"],
                    result["dependencia"],
                    result["cod_examen"],
                    result["aula"],
                    result["estado_resultado"],
                ]
            )
        ),
        dependencia_key=normalize_text(result["dependencia"]),
        estado_key=normalize_text(result["estado_resultado"]),
    )


def build_result_student_message(result: dict[str, Any], student: dict[str, Any] | None) -> str:
    state = str(result.get("estado_resultado", "") or "")
    salon_referencia = ""
    if student is not None:
        salon_referencia = str(student.get("salon", "") or "")

    if state == "sin_lectura":
        return (
            "Tu puntaje aparece en 0.00 porque el archivo no registró lectura de respuestas para tu DNI. "
            "Para la siguiente evaluación, revisa que marques bien tu ficha y tus datos antes de entregar el examen."
        )
    if state == "puntaje_vacio":
        return (
            "Tu puntaje aparece en 0.00 porque el archivo llegó sin puntaje registrado para tu DNI. "
            "Para la siguiente evaluación, verifica tus datos y consulta con coordinación si vuelve a ocurrir."
        )
    if state == "puntaje_cero":
        return (
            "Tu puntaje oficial es 0.00 según el archivo registrado. "
            "Para la siguiente evaluación, revisa con calma el marcado de respuestas y tus datos antes de entregar el examen."
        )
    if state == "aula_vacia":
        if salon_referencia:
            return (
                f"Tu puntaje fue registrado, pero el aula no figura en el archivo de resultados. "
                f"Como referencia, en el padrón apareces en {salon_referencia}. "
                "Para la siguiente evaluación, verifica que tu aula y tus datos queden bien registrados."
            )
        return (
            "Tu puntaje fue registrado, pero el aula no figura en el archivo de resultados. "
            "Para la siguiente evaluación, verifica que tu aula y tus datos queden bien registrados."
        )
    return (
        "Tu resultado fue registrado correctamente. "
        "Verifica tus datos personales antes de la siguiente evaluación."
    )


def serialize_result(result: dict[str, Any], students_by_dni: dict[str, dict[str, Any]]) -> dict[str, Any]:
    student = students_by_dni.get(result["dni"])
    payload = dict(result)
    payload["mensaje_estudiante"] = build_result_student_message(result, student)
    payload["alumno"] = None if student is None else dict(student)
    return payload


def serialize_public_result(result: dict[str, Any], students_by_dni: dict[str, dict[str, Any]]) -> dict[str, Any]:
    student = students_by_dni.get(result["dni"])
    area = ""
    sede = ""
    salon_referencia = ""
    if student is not None:
        area = str(student.get("area", "") or "")
        sede = str(student.get("sede", "") or "")
        salon_referencia = str(student.get("salon", "") or "")

    return {
        "dni": result["dni"],
        "nombre_completo": result["nombre_completo"],
        "puntaje": float(result["puntaje"]),
        "dependencia": result["dependencia"],
        "area": area,
        "sede": sede,
        "aula": result["aula"],
        "salon_referencia": salon_referencia,
        "estado_resultado": result["estado_resultado"],
        "mensaje_estudiante": build_result_student_message(result, student),
    }


def build_summary(students: list[dict[str, Any]]) -> dict[str, Any]:
    by_area = dict(sorted(Counter(student["area"] for student in students).items()))
    by_sede = dict(sorted(Counter(student["sede"] for student in students).items()))
    areas = list(by_area.keys())
    sedes = list(by_sede.keys())
    return {
        "total_alumnos": len(students),
        "total_areas": len(areas),
        "total_salones": len({student["salon"] for student in students}),
        "total_sedes": len(sedes),
        "areas": areas,
        "sedes": sedes,
        "alumnos_por_area": by_area,
        "alumnos_por_sede": by_sede,
    }


class JsonStudentRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dataset: dict[str, Any] | None = None
        self._students: list[dict[str, Any]] = []
        self._students_by_dni: dict[str, dict[str, Any]] = {}
        self._indexed_students: list[IndexedStudent] = []
        self._results: list[dict[str, Any]] = []
        self._results_by_dni: dict[str, dict[str, Any]] = {}
        self._indexed_results: list[IndexedResult] = []
        self._results_summary: dict[str, Any] | None = None
        self._serialized_results_by_dni: dict[str, dict[str, Any]] = {}
        self._public_results_by_dni: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        self._ensure_dataset_file()

        with self.settings.data_json_path.open("r", encoding="utf-8") as source:
            self.dataset = json.load(source)

        self._students = list(self.dataset.get("alumnos", []))
        self._results = [normalize_result_record(item) for item in self.dataset.get("resultados", [])]
        self._rebuild_indexes()

    def summary(self) -> dict[str, Any]:
        event = self._event_payload()
        summary = self._summary_payload()
        return {
            **summary,
            "evento": event,
            "source_file": self.dataset.get("source_file", "") if self.dataset else "",
            "generated_at": self.dataset.get("generated_at", "") if self.dataset else "",
        }

    def results_summary(self) -> dict[str, Any]:
        if self._results_summary is None:
            self._results_summary = build_results_summary(self._results)
        return {
            **self._results_summary,
            "source_file": self.dataset.get("resultados_source_file", "") if self.dataset else "",
            "generated_at": self.dataset.get("resultados_generated_at", "") if self.dataset else "",
        }

    def get_student_by_dni(self, dni: str) -> dict[str, Any] | None:
        student = self._students_by_dni.get(dni)
        return None if student is None else dict(student)

    def get_result_by_dni(self, dni: str) -> dict[str, Any] | None:
        payload = self._serialized_results_by_dni.get(dni)
        return None if payload is None else dict(payload)

    def get_public_result_by_dni(self, dni: str) -> dict[str, Any] | None:
        payload = self._public_results_by_dni.get(dni)
        return None if payload is None else dict(payload)

    def search_students(
        self,
        *,
        dni: str | None = None,
        q: str | None = None,
        area: str | None = None,
        salon: str | None = None,
        sede: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[int, list[dict[str, Any]]]:
        if limit is None:
            limit = self.settings.default_limit

        exact_match: list[IndexedStudent]
        if dni:
            student = self._students_by_dni.get(dni)
            exact_match = [] if student is None else [build_indexed_student(student)]
        else:
            exact_match = list(self._indexed_students)

        query_key = normalize_text(q) if q else None
        area_key = normalize_text(area) if area else None
        salon_key = normalize_text(salon) if salon else None
        sede_key = normalize_text(sede) if sede else None

        filtered: list[dict[str, Any]] = []
        for item in exact_match:
            if query_key and query_key not in item.search_blob:
                continue
            if area_key and area_key != item.area_key:
                continue
            if salon_key and salon_key != item.salon_key:
                continue
            if sede_key and sede_key != item.sede_key:
                continue
            filtered.append(dict(item.student))

        total = len(filtered)
        return total, filtered[offset : offset + limit]

    def search_results(
        self,
        *,
        dni: str | None = None,
        q: str | None = None,
        dependencia: str | None = None,
        estado_resultado: str | None = None,
        only_zero: bool = False,
        puntaje_min: float | None = None,
        puntaje_max: float | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[int, list[dict[str, Any]]]:
        if limit is None:
            limit = self.settings.default_limit

        exact_match: list[IndexedResult]
        if dni:
            result = self._results_by_dni.get(dni)
            exact_match = [] if result is None else [build_indexed_result(result)]
        else:
            exact_match = list(self._indexed_results)

        query_key = normalize_text(q) if q else None
        dependencia_key = normalize_text(dependencia) if dependencia else None
        estado_key = normalize_text(estado_resultado) if estado_resultado else None

        filtered: list[dict[str, Any]] = []
        for item in exact_match:
            result = item.result
            if query_key and query_key not in item.search_blob:
                continue
            if dependencia_key and dependencia_key != item.dependencia_key:
                continue
            if estado_key and estado_key != item.estado_key:
                continue
            if only_zero and not result["puntaje_es_cero"]:
                continue
            if puntaje_min is not None and float(result["puntaje"]) < puntaje_min:
                continue
            if puntaje_max is not None and float(result["puntaje"]) > puntaje_max:
                continue
            filtered.append(serialize_result(result, self._students_by_dni))

        total = len(filtered)
        return total, filtered[offset : offset + limit]

    def create_student(self, student: dict[str, Any]) -> dict[str, Any]:
        if student["dni"] in self._students_by_dni:
            raise DuplicateStudentError(f"Ya existe un alumno con DNI {student['dni']}.")

        record = dict(student)
        self._students.append(record)
        self._rebuild_indexes()
        self._persist()
        return dict(record)

    def update_student(self, dni: str, student: dict[str, Any]) -> dict[str, Any] | None:
        current = self._students_by_dni.get(dni)
        if current is None:
            return None

        current.update(student)
        self._rebuild_indexes()
        self._persist()
        return dict(current)

    def delete_student(self, dni: str) -> bool:
        if dni not in self._students_by_dni:
            return False

        self._students = [student for student in self._students if student["dni"] != dni]
        self._rebuild_indexes()
        self._persist()
        return True

    def update_attendance(self, dni: str, asistencia: bool) -> dict[str, Any] | None:
        current = self._students_by_dni.get(dni)
        if current is None:
            return None

        current["asistencia"] = bool(asistencia)
        self._persist()
        return dict(current)

    def _ensure_dataset_file(self) -> None:
        dataset_exists = self.settings.data_json_path.exists()

        if not dataset_exists:
            if self.settings.excel_path is None or not self.settings.excel_path.exists():
                raise FileNotFoundError(
                    "No data source found. Set SIMULACRO_EXCEL_PATH or generate the JSON dataset first."
                )
            build_dataset(
                self.settings.excel_path,
                self.settings.data_json_path,
                results_excel_path=self.settings.results_excel_path if self.settings.results_excel_path and self.settings.results_excel_path.exists() else None,
            )
            return

        source_mtimes = [self.settings.data_json_path.stat().st_mtime]
        latest_source_mtime = source_mtimes[0]
        if self.settings.excel_path is not None and self.settings.excel_path.exists():
            latest_source_mtime = max(latest_source_mtime, self.settings.excel_path.stat().st_mtime)
        if self.settings.results_excel_path is not None and self.settings.results_excel_path.exists():
            latest_source_mtime = max(latest_source_mtime, self.settings.results_excel_path.stat().st_mtime)

        if (
            self.settings.auto_rebuild_data
            and latest_source_mtime > self.settings.data_json_path.stat().st_mtime
        ):
            if self.settings.excel_path is None or not self.settings.excel_path.exists():
                raise FileNotFoundError(
                    "No student source Excel was found to rebuild the JSON dataset."
                )
            build_dataset(
                self.settings.excel_path,
                self.settings.data_json_path,
                results_excel_path=self.settings.results_excel_path if self.settings.results_excel_path and self.settings.results_excel_path.exists() else None,
            )

    def _rebuild_indexes(self) -> None:
        for student in self._students:
            student["asistencia"] = bool(student.get("asistencia", False))
        self._students_by_dni = {student["dni"]: student for student in self._students}
        self._indexed_students = [build_indexed_student(student) for student in self._students]
        self._results = [normalize_result_record(result) for result in self._results]
        self._results_by_dni = {result["dni"]: result for result in self._results}
        self._indexed_results = [build_indexed_result(result) for result in self._results]
        self._results_summary = build_results_summary(self._results)
        self._serialized_results_by_dni = {
            result["dni"]: serialize_result(result, self._students_by_dni) for result in self._results
        }
        self._public_results_by_dni = {
            result["dni"]: serialize_public_result(result, self._students_by_dni) for result in self._results
        }

    def _event_payload(self) -> dict[str, Any]:
        if self.dataset is None:
            raise RuntimeError("Repository has not been loaded.")
        return dict(self.dataset.get("evento", {}))

    def _summary_payload(self) -> dict[str, Any]:
        return build_summary(self._students)

    def _persist(self) -> None:
        if self.dataset is None:
            raise RuntimeError("Repository has not been loaded.")

        self.dataset["alumnos"] = self._students
        self.dataset["resumen"] = self._summary_payload()
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.settings.data_json_path.write_text(
            json.dumps(self.dataset, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class MySQLStudentRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache_lock = Lock()
        self._students: list[dict[str, Any]] = []
        self._students_by_dni: dict[str, dict[str, Any]] = {}
        self._indexed_students: list[IndexedStudent] = []
        self._results: list[dict[str, Any]] = []
        self._results_by_dni: dict[str, dict[str, Any]] = {}
        self._indexed_results: list[IndexedResult] = []
        self._summary: dict[str, Any] | None = None
        self._results_summary: dict[str, Any] | None = None
        self._serialized_results_by_dni: dict[str, dict[str, Any]] = {}
        self._public_results_by_dni: dict[str, dict[str, Any]] = {}
        self._event: dict[str, Any] = {
            "institucion": "",
            "organizador": "",
            "titulo": "",
        }
        self._source_file = ""
        self._generated_at = ""
        self._results_source_file = ""
        self._results_generated_at = ""
        self._snapshot_loaded_at = 0.0

    def load(self) -> None:
        if not mysql_is_configured(self.settings):
            raise RuntimeError(
                "MySQL backend selected, but the connection variables are incomplete. "
                "Review SIMULACRO_DB_HOST, SIMULACRO_DB_NAME, SIMULACRO_DB_USER and SIMULACRO_DB_PASSWORD."
            )

        ensure_mysql_schema(self.settings)

        with get_mysql_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) AS total FROM `{self.settings.db_students_table}`")
                total_students = int(cursor.fetchone()["total"])
                cursor.execute(f"SELECT COUNT(*) AS total FROM `{self.settings.db_results_table}`")
                total_results = int(cursor.fetchone()["total"])

        should_seed_students = total_students == 0
        should_seed_results = total_results == 0 and self.settings.results_excel_path and self.settings.results_excel_path.exists()
        if (
            self.settings.db_auto_seed
            and self.settings.excel_path
            and self.settings.excel_path.exists()
            and (should_seed_students or should_seed_results)
        ):
            dataset = build_dataset(
                self.settings.excel_path,
                results_excel_path=self.settings.results_excel_path if self.settings.results_excel_path and self.settings.results_excel_path.exists() else None,
            )
            load_dataset_to_mysql(dataset, self.settings, truncate=should_seed_students)

        self._refresh_snapshot(force=True)

    def summary(self) -> dict[str, Any]:
        self._ensure_snapshot()
        if self._summary is None:
            raise RuntimeError("MySQL cache has not been loaded.")
        return {
            **self._summary,
            "evento": dict(self._event),
            "source_file": self._source_file,
            "generated_at": self._generated_at,
        }

    def results_summary(self) -> dict[str, Any]:
        self._ensure_snapshot()
        if self._results_summary is None:
            self._results_summary = build_results_summary(self._results)
        return {
            **self._results_summary,
            "source_file": self._results_source_file,
            "generated_at": self._results_generated_at,
        }

    def get_student_by_dni(self, dni: str) -> dict[str, Any] | None:
        self._ensure_snapshot()
        student = self._students_by_dni.get(dni)
        return None if student is None else dict(student)

    def get_result_by_dni(self, dni: str) -> dict[str, Any] | None:
        self._ensure_snapshot()
        payload = self._serialized_results_by_dni.get(dni)
        return None if payload is None else dict(payload)

    def get_public_result_by_dni(self, dni: str) -> dict[str, Any] | None:
        self._ensure_snapshot()
        payload = self._public_results_by_dni.get(dni)
        return None if payload is None else dict(payload)

    def search_students(
        self,
        *,
        dni: str | None = None,
        q: str | None = None,
        area: str | None = None,
        salon: str | None = None,
        sede: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[int, list[dict[str, Any]]]:
        self._ensure_snapshot()
        if limit is None:
            limit = self.settings.default_limit

        exact_match: list[IndexedStudent]
        if dni:
            student = self._students_by_dni.get(dni)
            exact_match = [] if student is None else [build_indexed_student(student)]
        else:
            exact_match = list(self._indexed_students)

        query_key = normalize_text(q) if q else None
        area_key = normalize_text(area) if area else None
        salon_key = normalize_text(salon) if salon else None
        sede_key = normalize_text(sede) if sede else None

        filtered: list[dict[str, Any]] = []
        for item in exact_match:
            if query_key and query_key not in item.search_blob:
                continue
            if area_key and area_key != item.area_key:
                continue
            if salon_key and salon_key != item.salon_key:
                continue
            if sede_key and sede_key != item.sede_key:
                continue
            filtered.append(dict(item.student))

        total = len(filtered)
        return total, filtered[offset : offset + limit]

    def search_results(
        self,
        *,
        dni: str | None = None,
        q: str | None = None,
        dependencia: str | None = None,
        estado_resultado: str | None = None,
        only_zero: bool = False,
        puntaje_min: float | None = None,
        puntaje_max: float | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[int, list[dict[str, Any]]]:
        self._ensure_snapshot()
        if limit is None:
            limit = self.settings.default_limit

        exact_match: list[IndexedResult]
        if dni:
            result = self._results_by_dni.get(dni)
            exact_match = [] if result is None else [build_indexed_result(result)]
        else:
            exact_match = list(self._indexed_results)

        query_key = normalize_text(q) if q else None
        dependencia_key = normalize_text(dependencia) if dependencia else None
        estado_key = normalize_text(estado_resultado) if estado_resultado else None

        filtered: list[dict[str, Any]] = []
        for item in exact_match:
            result = item.result
            if query_key and query_key not in item.search_blob:
                continue
            if dependencia_key and dependencia_key != item.dependencia_key:
                continue
            if estado_key and estado_key != item.estado_key:
                continue
            if only_zero and not result["puntaje_es_cero"]:
                continue
            if puntaje_min is not None and float(result["puntaje"]) < puntaje_min:
                continue
            if puntaje_max is not None and float(result["puntaje"]) > puntaje_max:
                continue
            filtered.append(serialize_result(result, self._students_by_dni))

        total = len(filtered)
        return total, filtered[offset : offset + limit]

    def create_student(self, student: dict[str, Any]) -> dict[str, Any]:
        self._ensure_snapshot()
        if self.get_student_by_dni(student["dni"]) is not None:
            raise DuplicateStudentError(f"Ya existe un alumno con DNI {student['dni']}.")

        with get_mysql_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO `{self.settings.db_students_table}` (
                        numero_orden, dni, nombre, sede, area, salon,
                        sede_key, area_key, salon_key,
                        capacidad_salon, fila_excel, asistencia, search_text
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    self._payload_to_db_row(student),
                )
            connection.commit()

        self._refresh_snapshot(force=True)
        created = self._students_by_dni.get(student["dni"])
        if created is None:
            raise RuntimeError("The student was inserted but could not be fetched afterwards.")
        return dict(created)

    def update_student(self, dni: str, student: dict[str, Any]) -> dict[str, Any] | None:
        self._ensure_snapshot()
        current = self._students_by_dni.get(dni)
        if current is None:
            return None

        merged = {**current, **student, "dni": dni}

        with get_mysql_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE `{self.settings.db_students_table}`
                    SET numero_orden = %s,
                        nombre = %s,
                        sede = %s,
                        area = %s,
                        salon = %s,
                        sede_key = %s,
                        area_key = %s,
                        salon_key = %s,
                        capacidad_salon = %s,
                        fila_excel = %s,
                        asistencia = %s,
                        search_text = %s
                    WHERE dni = %s
                    """,
                    (
                        merged["numero_orden"],
                        merged["nombre"],
                        merged["sede"],
                        merged["area"],
                        merged["salon"],
                        normalize_text(merged["sede"]),
                        normalize_text(merged["area"]),
                        normalize_text(merged["salon"]),
                        merged["capacidad_salon"],
                        merged["fila_excel"],
                        int(bool(merged["asistencia"])),
                        normalize_text(" ".join([merged["dni"], merged["nombre"], merged["sede"], merged["area"], merged["salon"]])),
                        dni,
                    ),
                )
            connection.commit()

        self._refresh_snapshot(force=True)
        updated = self._students_by_dni.get(dni)
        return None if updated is None else dict(updated)

    def delete_student(self, dni: str) -> bool:
        with get_mysql_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM `{self.settings.db_students_table}` WHERE dni = %s",
                    (dni,),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
        if deleted:
            self._refresh_snapshot(force=True)
        return deleted

    def update_attendance(self, dni: str, asistencia: bool) -> dict[str, Any] | None:
        self._ensure_snapshot()
        current = self._students_by_dni.get(dni)
        if current is None:
            return None

        with get_mysql_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE `{self.settings.db_students_table}`
                    SET asistencia = %s
                    WHERE dni = %s
                    """,
                    (int(bool(asistencia)), dni),
                )
            connection.commit()

        with self._cache_lock:
            cached = self._students_by_dni.get(dni)
            if cached is not None:
                cached["asistencia"] = bool(asistencia)
            for student in self._students:
                if student["dni"] == dni:
                    student["asistencia"] = bool(asistencia)
                    break
            self._snapshot_loaded_at = monotonic()

        updated = self._students_by_dni.get(dni)
        return None if updated is None else dict(updated)

    def _map_student(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "numero_orden": int(row["numero_orden"]),
            "dni": str(row["dni"]),
            "nombre": row["nombre"],
            "sede": row["sede"],
            "area": row["area"],
            "salon": row["salon"],
            "capacidad_salon": int(row["capacidad_salon"]),
            "fila_excel": int(row["fila_excel"]),
            "asistencia": bool(row.get("asistencia", 0)),
        }

    def _map_result(self, row: dict[str, Any]) -> dict[str, Any]:
        return normalize_result_record(
            {
                "dni": row["dni"],
                "paterno": row["paterno"],
                "materno": row["materno"],
                "nombres": row["nombres"],
                "nombre_completo": row["nombre_completo"],
                "cod_plaza": row["cod_plaza"],
                "plaza": row["plaza"],
                "dependencia": row["dependencia"],
                "aula": row["aula"],
                "litho_ide": row["litho_ide"],
                "lectura_nro_ide": row["lectura_nro_ide"],
                "cod_examen": row["cod_examen"],
                "litho_res": row["litho_res"],
                "lectura_nro_res": row["lectura_nro_res"],
                "respuestas": row["respuestas"],
                "puntaje": float(row["puntaje"]),
                "puntaje_reportado": None if row.get("puntaje_reportado") is None else float(row["puntaje_reportado"]),
                "puntaje_original": row["puntaje_original"],
                "puntaje_fue_completado": bool(row.get("puntaje_fue_completado", 0)),
                "puntaje_es_cero": bool(row.get("puntaje_es_cero", 0)),
                "respuestas_vacias": bool(row.get("respuestas_vacias", 0)),
                "estado_resultado": row["estado_resultado"],
                "fila_excel": int(row["fila_excel"]),
            }
        )

    def _payload_to_db_row(self, student: dict[str, Any]) -> tuple[Any, ...]:
        return (
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
            int(bool(student["asistencia"])),
            normalize_text(" ".join([student["dni"], student["nombre"], student["sede"], student["area"], student["salon"]])),
        )

    def _ensure_snapshot(self) -> None:
        ttl = max(0, self.settings.mysql_cache_ttl_seconds)
        if self._snapshot_loaded_at == 0:
            self._refresh_snapshot(force=True)
            return
        if ttl == 0:
            self._refresh_snapshot(force=True)
            return
        if monotonic() - self._snapshot_loaded_at >= ttl:
            self._refresh_snapshot(force=True)

    def _refresh_snapshot(self, *, force: bool = False) -> None:
        with self._cache_lock:
            ttl = max(0, self.settings.mysql_cache_ttl_seconds)
            if not force and self._snapshot_loaded_at and ttl > 0 and monotonic() - self._snapshot_loaded_at < ttl:
                return

            with get_mysql_connection(self.settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT numero_orden, dni, nombre, sede, area, salon, capacidad_salon, fila_excel, asistencia
                        FROM `{self.settings.db_students_table}`
                        ORDER BY area, salon, numero_orden, nombre
                        """
                    )
                    students = [self._map_student(row) for row in cursor.fetchall()]

                    cursor.execute(
                        f"""
                        SELECT
                            institucion,
                            organizador,
                            titulo,
                            source_file,
                            generated_at,
                            results_source_file,
                            results_generated_at
                        FROM `{self.settings.db_event_table}`
                        WHERE id = 1
                        """
                    )
                    event_row = cursor.fetchone() or {}

                    cursor.execute(
                        f"""
                        SELECT
                            dni, paterno, materno, nombres, nombre_completo,
                            cod_plaza, plaza, dependencia, aula,
                            litho_ide, lectura_nro_ide, cod_examen,
                            litho_res, lectura_nro_res, respuestas,
                            puntaje, puntaje_reportado, puntaje_original,
                            puntaje_fue_completado, puntaje_es_cero, respuestas_vacias,
                            estado_resultado, fila_excel
                        FROM `{self.settings.db_results_table}`
                        ORDER BY fila_excel, dni
                        """
                    )
                    results = [self._map_result(row) for row in cursor.fetchall()]

            self._students = students
            self._students_by_dni = {student["dni"]: student for student in students}
            self._indexed_students = [build_indexed_student(student) for student in students]
            self._summary = build_summary(students)
            self._results = results
            self._results_by_dni = {result["dni"]: result for result in results}
            self._indexed_results = [build_indexed_result(result) for result in results]
            self._results_summary = build_results_summary(results)
            self._serialized_results_by_dni = {
                result["dni"]: serialize_result(result, self._students_by_dni) for result in results
            }
            self._public_results_by_dni = {
                result["dni"]: serialize_public_result(result, self._students_by_dni) for result in results
            }
            self._event = {
                "institucion": event_row.get("institucion", ""),
                "organizador": event_row.get("organizador", ""),
                "titulo": event_row.get("titulo", ""),
            }
            self._source_file = event_row.get("source_file", "") or ""
            self._generated_at = str(event_row.get("generated_at", "") or "")
            self._results_source_file = event_row.get("results_source_file", "") or ""
            self._results_generated_at = str(event_row.get("results_generated_at", "") or "")
            self._snapshot_loaded_at = monotonic()


def create_repository(settings: Settings) -> Repository:
    if settings.storage_backend == "mysql":
        return MySQLStudentRepository(settings)
    return JsonStudentRepository(settings)

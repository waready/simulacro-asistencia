from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AlumnoBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    numero_orden: int = Field(ge=1, description="Posicion del alumno dentro del salon.")
    nombre: str = Field(min_length=1, max_length=255)
    sede: str = Field(min_length=1, max_length=120)
    area: str = Field(min_length=1, max_length=120)
    salon: str = Field(min_length=1, max_length=60)
    capacidad_salon: int = Field(ge=1, le=1000)
    fila_excel: int = Field(default=0, ge=0)
    asistencia: bool = False


class AlumnoCreateRequest(AlumnoBase):
    dni: str = Field(pattern=r"^\d{8,16}$")


class AlumnoUpdateRequest(AlumnoBase):
    pass


class Alumno(BaseModel):
    numero_orden: int = Field(description="Posicion del alumno dentro del salon.")
    dni: str
    nombre: str
    sede: str
    area: str
    salon: str
    capacidad_salon: int
    fila_excel: int
    asistencia: bool


class AsistenciaUpdateRequest(BaseModel):
    asistencia: bool


class PanelAttendanceRequest(BaseModel):
    dni: str = Field(pattern=r"^\d{8,16}$")


class BusquedaResponse(BaseModel):
    total: int
    limite: int
    offset: int
    items: list[Alumno]


class MensajeResponse(BaseModel):
    detail: str


class PanelAttendanceResponse(BaseModel):
    detail: str
    updated: bool
    already_marked: bool
    alumno: Alumno


class Evento(BaseModel):
    institucion: str
    organizador: str
    titulo: str


class ResumenResponse(BaseModel):
    total_alumnos: int
    total_areas: int
    total_salones: int
    total_sedes: int
    areas: list[str]
    sedes: list[str]
    alumnos_por_area: dict[str, int]
    alumnos_por_sede: dict[str, int]
    evento: Evento
    source_file: str
    generated_at: str


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    storage: str

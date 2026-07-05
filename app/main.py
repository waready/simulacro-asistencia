from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.repository import DuplicateStudentError, Repository, create_repository
from app.schemas import (
    Alumno,
    AlumnoCreateRequest,
    AlumnoUpdateRequest,
    AsistenciaUpdateRequest,
    BusquedaResponse,
    HealthResponse,
    MensajeResponse,
    PanelAttendanceRequest,
    PanelAttendanceResponse,
    ResultadoConsultaResponse,
    Resultado,
    ResultadoBusquedaResponse,
    ResultadoResumenResponse,
    ResumenResponse,
)

settings = get_settings()
PANEL_HTML_PATH = Path(__file__).resolve().parent / "panel.html"
RESULTS_HTML_PATH = Path(__file__).resolve().parent / "resultados.html"
STATIC_DIR_PATH = Path(__file__).resolve().parent / "static"


class CacheControlStaticFiles(StaticFiles):
    def __init__(self, *args, cache_control: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cache_control = cache_control

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == status.HTTP_200_OK:
            response.headers.setdefault("Cache-Control", self.cache_control)
        return response


def validate_api_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None),
) -> None:
    if not settings.api_key:
        return

    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository = create_repository(settings)
    repository.load()
    app.state.repository = repository
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API para consultar la asignacion de alumnos del simulacro desde Laravel u otros servicios.",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=list(settings.cors_methods),
        allow_headers=list(settings.cors_headers),
    )

app.add_middleware(GZipMiddleware, minimum_size=1024)

app.mount(
    "/static",
    CacheControlStaticFiles(
        directory=STATIC_DIR_PATH,
        cache_control=f"public, max-age={max(0, settings.static_cache_seconds)}, immutable",
    ),
    name="static",
)


def get_repository(request: Request) -> Repository:
    return request.app.state.repository


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/resultados", methods=["GET", "HEAD"], include_in_schema=False)
def portal_resultados() -> FileResponse:
    return FileResponse(
        RESULTS_HTML_PATH,
        headers={"Cache-Control": f"public, max-age={max(0, settings.public_portal_cache_seconds)}"},
    )


@app.api_route("/panel", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/asistencia", methods=["GET", "HEAD"], include_in_schema=False)
def panel_inicio() -> FileResponse:
    return FileResponse(
        PANEL_HTML_PATH,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.post("/api/panel/asistencia", response_model=PanelAttendanceResponse, include_in_schema=False)
@app.post("/panel/api/asistencia", response_model=PanelAttendanceResponse, include_in_schema=False)
def panel_marcar_asistencia(
    payload: PanelAttendanceRequest,
    repository: Repository = Depends(get_repository),
) -> PanelAttendanceResponse:
    student = repository.get_student_by_dni(payload.dni)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumno no encontrado.")

    already_marked = bool(student.get("asistencia", False))
    if already_marked:
        updated_student = student
        detail = "Asistencia ya registrada."
    else:
        updated_student = repository.update_attendance(payload.dni, True)
        if updated_student is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumno no encontrado.")
        detail = "Asistencia registrada correctamente."

    return PanelAttendanceResponse(
        detail=detail,
        updated=not already_marked,
        already_marked=already_marked,
        alumno=Alumno.model_validate(updated_student),
    )


@app.get("/api", dependencies=[Depends(validate_api_key)])
def api_root() -> dict[str, str]:
    return {
        "message": "Simulacro API activa.",
        "docs": "/docs",
        "health": "/health",
        "panel": "/panel",
        "portal_resultados": "/",
        "resumen": "/api/v1/resumen",
        "resultados": "/api/v1/resultados",
        "resultados_resumen": "/api/v1/resultados/resumen",
        "storage": settings.storage_backend,
    }


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(validate_api_key)])
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        storage=settings.storage_backend,
    )


@app.get("/api/v1/resumen", response_model=ResumenResponse, dependencies=[Depends(validate_api_key)])
def resumen(repository: Repository = Depends(get_repository)) -> ResumenResponse:
    return ResumenResponse.model_validate(repository.summary())


@app.get(
    "/api/v1/resultados/resumen",
    response_model=ResultadoResumenResponse,
    dependencies=[Depends(validate_api_key)],
)
def resumen_resultados(repository: Repository = Depends(get_repository)) -> ResultadoResumenResponse:
    return ResultadoResumenResponse.model_validate(repository.results_summary())


@app.get(
    "/api/public/resultados/{dni}",
    response_model=ResultadoConsultaResponse,
)
def resultado_publico_por_dni(
    dni: str,
    repository: Repository = Depends(get_repository),
) -> ResultadoConsultaResponse:
    result = repository.get_public_result_by_dni(dni)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resultado no encontrado.")
    return JSONResponse(
        content=result,
        headers={
            "Cache-Control": (
                f"public, max-age={max(0, settings.public_results_cache_seconds)}, "
                "stale-while-revalidate=60, stale-if-error=600"
            )
        },
    )


@app.get(
    "/api/v1/resultados/{dni}",
    response_model=Resultado,
    dependencies=[Depends(validate_api_key)],
)
def resultado_por_dni(dni: str, repository: Repository = Depends(get_repository)) -> Resultado:
    result = repository.get_result_by_dni(dni)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resultado no encontrado.")
    return Resultado.model_validate(result)


@app.get(
    "/api/v1/resultados",
    response_model=ResultadoBusquedaResponse,
    dependencies=[Depends(validate_api_key)],
)
def buscar_resultados(
    repository: Repository = Depends(get_repository),
    dni: str | None = Query(default=None, description="Busqueda exacta por DNI."),
    q: str | None = Query(
        default=None,
        description="Busqueda libre por DNI, nombre, dependencia, aula o estado del resultado.",
    ),
    dependencia: str | None = Query(default=None, description="Filtro exacto por dependencia."),
    estado_resultado: str | None = Query(
        default=None,
        description="Filtro exacto por estado: ok, sin_lectura, puntaje_vacio, aula_vacia o puntaje_cero.",
    ),
    solo_cero: bool = Query(
        default=False,
        description="Si es true, devuelve solo resultados con puntaje final 0, incluidos los normalizados desde vacio.",
    ),
    puntaje_min: float | None = Query(default=None, description="Filtro por puntaje minimo."),
    puntaje_max: float | None = Query(default=None, description="Filtro por puntaje maximo."),
    limit: int = Query(default=settings.default_limit, ge=1),
    offset: int = Query(default=0, ge=0),
) -> ResultadoBusquedaResponse:
    safe_limit = min(limit, settings.max_limit)
    total, items = repository.search_results(
        dni=dni,
        q=q,
        dependencia=dependencia,
        estado_resultado=estado_resultado,
        only_zero=solo_cero,
        puntaje_min=puntaje_min,
        puntaje_max=puntaje_max,
        limit=safe_limit,
        offset=offset,
    )
    return ResultadoBusquedaResponse(
        total=total,
        limite=safe_limit,
        offset=offset,
        items=[Resultado.model_validate(item) for item in items],
    )


@app.get("/api/v1/alumnos/{dni}", response_model=Alumno, dependencies=[Depends(validate_api_key)])
def alumno_por_dni(dni: str, repository: Repository = Depends(get_repository)) -> Alumno:
    student = repository.get_student_by_dni(dni)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumno no encontrado.")
    return Alumno.model_validate(student)


@app.post(
    "/api/v1/alumnos",
    response_model=Alumno,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_api_key)],
)
def crear_alumno(
    payload: AlumnoCreateRequest,
    repository: Repository = Depends(get_repository),
) -> Alumno:
    try:
        student = repository.create_student(payload.model_dump())
    except DuplicateStudentError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Alumno.model_validate(student)


@app.put("/api/v1/alumnos/{dni}", response_model=Alumno, dependencies=[Depends(validate_api_key)])
def actualizar_alumno(
    dni: str,
    payload: AlumnoUpdateRequest,
    repository: Repository = Depends(get_repository),
) -> Alumno:
    student = repository.update_student(dni, payload.model_dump())
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumno no encontrado.")
    return Alumno.model_validate(student)


@app.delete(
    "/api/v1/alumnos/{dni}",
    response_model=MensajeResponse,
    dependencies=[Depends(validate_api_key)],
)
def eliminar_alumno(dni: str, repository: Repository = Depends(get_repository)) -> MensajeResponse:
    deleted = repository.delete_student(dni)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumno no encontrado.")
    return MensajeResponse(detail=f"Alumno con DNI {dni} eliminado.")


@app.patch("/api/v1/alumnos/{dni}/asistencia", response_model=Alumno, dependencies=[Depends(validate_api_key)])
def actualizar_asistencia(
    dni: str,
    payload: AsistenciaUpdateRequest,
    repository: Repository = Depends(get_repository),
) -> Alumno:
    student = repository.update_attendance(dni, payload.asistencia)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alumno no encontrado.")
    return Alumno.model_validate(student)


@app.get("/api/v1/alumnos", response_model=BusquedaResponse, dependencies=[Depends(validate_api_key)])
def buscar_alumnos(
    repository: Repository = Depends(get_repository),
    dni: str | None = Query(default=None, description="Busqueda exacta por DNI."),
    q: str | None = Query(default=None, description="Busqueda libre por nombre, sede, area o salon."),
    area: str | None = Query(default=None),
    salon: str | None = Query(default=None),
    sede: str | None = Query(default=None),
    limit: int = Query(default=settings.default_limit, ge=1),
    offset: int = Query(default=0, ge=0),
) -> BusquedaResponse:
    safe_limit = min(limit, settings.max_limit)
    total, items = repository.search_students(
        dni=dni,
        q=q,
        area=area,
        salon=salon,
        sede=sede,
        limit=safe_limit,
        offset=offset,
    )
    return BusquedaResponse(
        total=total,
        limite=safe_limit,
        offset=offset,
        items=[Alumno.model_validate(item) for item in items],
    )

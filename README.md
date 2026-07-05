# Simulacro API

Microservicio en FastAPI para exponer el Excel del simulacro como una API lista para consumo desde Laravel.

## Que hace

- Lee el archivo `.xlsx` original sin `pandas` ni `openpyxl`.
- Puede trabajar en dos modos: `mysql` o `json`.
- En modo `mysql` crea y usa tablas dentro de tu base `simulacro_aulas`.
- En modo `json` deja un respaldo local por si quieres probar sin MySQL.
- Permite buscar por `dni`, nombre, sede, area o salon.
- Permite exponer resultados por `dni`, dependencia, estado o rango de puntaje.
- Puede proteger el acceso con `X-API-Key`.

## Estructura esperada

El Excel actual tiene bloques con este formato:

1. Titulo del evento.
2. Linea de area y salon.
3. Cabecera `N° | DNI | APELLIDOS Y NOMBRES | SEDE`.
4. Filas de alumnos.

## Variables de entorno

Usa `.env.example` como referencia:

- `SIMULACRO_STORAGE_BACKEND`: `mysql` para produccion o `json` para pruebas.
- `SIMULACRO_EXCEL_PATH`: ruta del Excel original.
- `SIMULACRO_RESULTS_EXCEL_PATH`: ruta del Excel de puntajes.
- `SIMULACRO_DATA_JSON`: ruta del JSON normalizado que genera la API.
- `SIMULACRO_API_KEY`: token opcional para asegurar el acceso.
- `SIMULACRO_AUTO_REBUILD_DATA`: si es `true`, regenera el JSON cuando el Excel cambia.
- `SIMULACRO_DEFAULT_LIMIT`: limite por defecto.
- `SIMULACRO_MAX_LIMIT`: limite maximo por consulta.
- `SIMULACRO_DB_HOST`, `SIMULACRO_DB_PORT`, `SIMULACRO_DB_NAME`, `SIMULACRO_DB_USER`, `SIMULACRO_DB_PASSWORD`: conexion MySQL.
- `SIMULACRO_DB_STUDENTS_TABLE`, `SIMULACRO_DB_EVENT_TABLE`, `SIMULACRO_DB_RESULTS_TABLE`: nombres de tablas.
- `SIMULACRO_DB_AUTO_SEED`: si es `true`, la API llena MySQL automaticamente cuando la tabla este vacia y exista el Excel.
- `SIMULACRO_MYSQL_CACHE_TTL_SECONDS`: segundos que dura el snapshot en memoria cuando usas MySQL.
- `SIMULACRO_CORS_ORIGINS`: origins permitidos por CORS, separados por coma.
- `SIMULACRO_CORS_METHODS`, `SIMULACRO_CORS_HEADERS`, `SIMULACRO_CORS_ALLOW_CREDENTIALS`: politica CORS.

## Ejecucion local

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --env-file .env
```

## Cargar el Excel a MySQL

La ruta recomendada para tu caso es `mysql`, porque ya tienes la base `simulacro_aulas`.

Importacion directa a MySQL:

```powershell
python -m app.mysql_loader --excel "C:\ruta\alumnos.xlsx" --results-excel "C:\ruta\puntajes.xlsx" --mode mysql
```

Si prefieres subirlo desde phpMyAdmin, genera primero un `.sql`:

```powershell
python -m app.mysql_loader --excel "C:\ruta\alumnos.xlsx" --results-excel "C:\ruta\puntajes.xlsx" --mode sql --out ".\data\simulacro_aulas_dump.sql"
```

Ese archivo lo puedes importar en phpMyAdmin dentro de la base `simulacro_aulas`.

## Respaldo JSON opcional

Si quieres generar el JSON manualmente para pruebas:

```powershell
python -m app.importer --excel "C:\ruta\alumnos.xlsx" --results-excel "C:\ruta\puntajes.xlsx" --out ".\data\simulacro_dataset.json"
```

## Endpoints principales

- `GET /health`
- `GET /`
- `GET /panel`
- `GET /asistencia`
- `GET /resultados`
- `POST /api/panel/asistencia`
- `GET /api/public/resultados/{dni}`
- `GET /api/v1/resumen`
- `GET /api/v1/resultados/resumen`
- `GET /api/v1/resultados/{dni}`
- `GET /api/v1/resultados?solo_cero=true`
- `GET /api/v1/resultados?dependencia=SOCIALES&puntaje_min=1000`
- `GET /api/v1/resultados?estado_resultado=sin_lectura`
- `GET /api/v1/alumnos/{dni}`
- `GET /api/v1/alumnos?dni=60681300`
- `GET /api/v1/alumnos?q=abado`
- `GET /api/v1/alumnos?area=Biomedicas&sede=Juliaca`
- `POST /api/v1/alumnos`
- `PUT /api/v1/alumnos/{dni}`
- `PATCH /api/v1/alumnos/{dni}/asistencia`
- `DELETE /api/v1/alumnos/{dni}`

`/` y `/resultados` sirven el portal HTML de consulta de resultados para estudiantes.

`/panel` y `/asistencia` sirven el panel HTML mobile-first para toma de asistencia. El panel consume `POST /api/panel/asistencia` en una sola llamada para buscar por DNI, marcar `asistencia=true` y devolver los datos del alumno para el modal de confirmacion.

La consulta por resultados usa `GET /api/public/resultados/{dni}` y muestra el puntaje junto con una observacion clara cuando el caso sea `0.00`, `sin_lectura`, `puntaje_vacio` o `aula_vacia`.

## API de resultados

El segundo Excel de puntajes se normaliza en la tabla `simulacro_resultados` y en el JSON bajo `resultados`.

Filtros disponibles en `GET /api/v1/resultados`:

- `dni`: busqueda exacta.
- `q`: busqueda libre por DNI, nombres, dependencia, aula o estado.
- `dependencia`: filtro exacto.
- `estado_resultado`: `ok`, `sin_lectura`, `puntaje_vacio`, `aula_vacia`, `puntaje_cero`.
- `solo_cero=true`: devuelve puntajes finales en `0`, incluidos los vacios normalizados.
- `puntaje_min` y `puntaje_max`: rango numerico.

Hallazgo en el Excel `05-07-2026_15-26-19_lista_postulantes - copia.xlsx`:

- Hay `3200` registros de resultados y coinciden con los `3200` DNIs del padron.
- No aparecieron puntajes `0` reportados explicitamente.
- Hay `257` filas con `PUNTAJE` vacio.
- De esas filas, `256` quedaron como `sin_lectura` y `1` como `puntaje_vacio`.
- Ademas hay `4` registros con `aula_vacia` pero con puntaje valido.
- Esas filas se normalizan a `puntaje=0` para poder filtrarlas sin perder trazabilidad.
- Cuando eso pasa, la API conserva `puntaje_reportado=null`, `puntaje_original=""` y marca `puntaje_fue_completado=false`.
- El campo `estado_resultado` ayuda a distinguir si el `0` viene de `sin_lectura`, `puntaje_vacio` o de un `puntaje_cero` real si apareciera despues.
- La respuesta del resultado incluye `mensaje_estudiante` para explicar el caso al alumno con un texto breve y directo.

Si defines `SIMULACRO_API_KEY`, envia el header:

```text
X-API-Key: tu_token
```

## CRUD de alumnos

Crear alumno:

```json
{
  "dni": "79999999",
  "numero_orden": 41,
  "nombre": "ALUMNO PRUEBA API",
  "sede": "Puno",
  "area": "BIOMEDICAS",
  "salon": "B-999",
  "capacidad_salon": 50,
  "fila_excel": 0,
  "asistencia": false
}
```

Actualizar alumno:

```json
{
  "numero_orden": 42,
  "nombre": "ALUMNO PRUEBA ACTUALIZADO",
  "sede": "Juliaca",
  "area": "SOCIALES",
  "salon": "S-999",
  "capacidad_salon": 55,
  "fila_excel": 0,
  "asistencia": true
}
```

Actualizar asistencia:

```json
{
  "asistencia": true
}
```

## Ejemplo desde Laravel

```php
use Illuminate\Support\Facades\Http;

$response = Http::withHeaders([
    'X-API-Key' => config('services.simulacro.key'),
])->get(config('services.simulacro.url').'/api/v1/alumnos/60681300');

$alumno = $response->json();
```

## Recomendacion de despliegue

Para este caso, el dataset es pequeno: alrededor de 3200 alumnos y 81 salones. El cuello de botella no sera la lectura, sino el pico de consultas.

Recomendacion practica:

- Si solo seran 6 operadores internos o unas pocas consultas concurrentes: un Droplet basico de `1 vCPU / 1 GB RAM` alcanza.
- Si esperas picos cuando publiquen resultados o Laravel va a consultar mucho al mismo tiempo: mejor `2 vCPU / 2 GB RAM`.
- Ejecuta `uvicorn` detras de `nginx` y levanta 2 workers si usas el plan de 2 GB.
- Si Laravel consulta por `dni`, agrega cache en Laravel por 30 a 120 segundos para bajar aun mas la carga.
- Cuando usas backend `mysql`, la API mantiene un snapshot en memoria y por defecto lo refresca cada `60` segundos. Eso hace que `GET /api/v1/alumnos/{dni}` sea mucho mas rapido.
- Ese mismo snapshot acelera `GET /api/v1/resultados/{dni}` y los filtros de puntajes.

## Recomendacion para tu arquitectura

Como comentaste que Laravel estara en un servidor potente y FastAPI en uno basico:

- Usa `FastAPI + MySQL` en el servidor basico solo como microservicio de consulta.
- Haz que Laravel consuma el endpoint y aplique `cache`.
- Si el MySQL `simulacro_aulas` ya existe en ese server, este proyecto puede poblarlo y consultarlo sin problema.
- Para esta carga, `1 vCPU / 1 GB RAM` sigue siendo razonable si Laravel cachea.
- Si vas a usar CRUD con varios workers y quieres consistencia casi inmediata entre procesos, baja `SIMULACRO_MYSQL_CACHE_TTL_SECONDS` a `5` o `10`. Si priorizas velocidad de lectura, `30` o `60` va bien.
- Si Laravel solo consume servidor a servidor, CORS no es obligatorio. Si el navegador alabara directo a FastAPI, configura `SIMULACRO_CORS_ORIGINS` con el dominio real.

Configuracion sugerida de arranque:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --env-file .env
```

En `deploy/` te deje dos ejemplos listos:

- `simulacro-api.service.example` para `systemd`
- `nginx-simulacro.conf.example` para exponer el servicio detras de `nginx`

## Notas operativas

- No subas el Excel ni el JSON a un repositorio publico porque contiene datos personales.
- Si el Excel cambia y estas en modo `mysql`, vuelve a correr `python -m app.mysql_loader --excel ... --mode mysql`.
- Si activas `SIMULACRO_DB_AUTO_SEED=true`, la API poblara MySQL solo cuando encuentre las tablas vacias.
- Si usaras varios workers, la `asistencia` puede tardar hasta el TTL en reflejarse entre procesos. Para este proyecto recomiendo `1 worker`.

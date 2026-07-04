# Despliegue en Ubuntu

## Rendimiento para consultas por DNI

La API ya esta preparada para consultas rapidas por `dni`:

- Existe un indice unico en `simulacro_alumnos.dni`.
- El endpoint dedicado es `GET /api/v1/alumnos/{dni}`.
- La API consulta MySQL localmente por `127.0.0.1`, asi que el acceso a base no cruza red.
- La ruta de busqueda exacta no usa `LIKE`, solo `WHERE dni = %s LIMIT 1`.
- En modo `mysql`, la API mantiene un snapshot en memoria y lo refresca segun `SIMULACRO_MYSQL_CACHE_TTL_SECONDS`.

Prueba local hecha antes del despliegue:

- `200` consultas HTTP secuenciales por `dni`
- promedio aproximado: `11.3 ms` por request en este entorno de prueba

## CORS

- Si Laravel consume servidor a servidor, CORS no es necesario.
- Si un frontend en navegador consumira FastAPI directo, configura `SIMULACRO_CORS_ORIGINS` con el dominio exacto.
- Ejemplo: `SIMULACRO_CORS_ORIGINS=https://app.tudominio.com,https://admin.tudominio.com`

## Archivos de apoyo

- `deploy/.env.production.example`
- `deploy/simulacro-api.service.example`
- `deploy/nginx-simulacro.conf.example`
- `deploy/install-ubuntu.sh.example`
- `deploy/post-receive.example`
- `deploy/push-production.ps1`

## Pasos

1. Copia el proyecto al servidor, por ejemplo a `/opt/simulacro-api`.
2. Crea `.env` desde `deploy/.env.production.example`.
3. Instala dependencias con un virtualenv.
4. Levanta `uvicorn` con `systemd`.
5. Publica la app detras de `nginx`.

## Arranque recomendado

Para un Droplet basico:

```bash
/opt/simulacro-api/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Para este proyecto recomiendo `--workers 1` porque ya responde rapido y asi la `asistencia` se refleja al instante en el mismo proceso.

## Despliegue rapido por Git

Flujo recomendado para este proyecto:

1. Mantener GitHub como `origin`.
2. Agregar el servidor como remoto `production`.
3. Dejar un repositorio bare en el server, por ejemplo `/opt/simulacro-api.git`.
4. Usar un `post-receive hook` para actualizar `/opt/simulacro-api` y reiniciar `simulacro-api`.

Comando habitual luego de la configuracion:

```powershell
.\deploy\push-production.ps1
```

Ese script hace:

- usa la llave de despliegue local guardada en `.deploy-keys/simulacro-production-agent.key`
- empuja `HEAD` hacia `production`
- dispara el hook del servidor
- reinicia la API automaticamente

De esa manera ya no hace falta volver a empaquetar el proyecto ni subir `.tar.gz`.

## Consumo recomendado desde Laravel

- Consumir `GET /api/v1/alumnos/{dni}` para consulta exacta.
- Enviar `X-API-Key`.
- Aplicar cache de `30` a `120` segundos en Laravel por `dni`.
- Reutilizar conexiones HTTP si usas un cliente compartido.

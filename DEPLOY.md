# Despliegue real (Render + Streamlit Cloud)

Objetivo: que la app use viajes GTFS reales y deje de mostrar el badge **Demo-Modus**.
Streamlit Cloud solo ejecuta `app.py`; la API Fastify, PostgreSQL y Redis van aparte en Render.

## 1. Crear el backend en Render

1. Entra en https://dashboard.render.com y conecta tu cuenta de GitHub.
2. **New → Blueprint** y elige el repositorio `dgoo24/SeatNow`.
3. Render detecta `render.yaml` y muestra:
   - `seatnow-api` (web Docker, Frankfurt, free)
   - `seatnow-cache` (Key Value)
   - `seatnow-db` (PostgreSQL, base `seatnow`)
4. Cuando pida `ADMIN_PASSWORD`, escribe una contraseña larga (mínimo 8 caracteres). Es la del usuario `admin@trenes.local`.
5. Pulsa **Apply** y espera el primer despliegue (build + arranque + importación GTFS, unos 5–10 minutos).

El contenedor hace todo solo en el primer arranque:

- sincroniza el esquema Prisma,
- crea el admin y el usuario `demo@trenes.local / demo1234`,
- importa GTFS desde `https://download.gtfs.de/germany/fv_free/latest.zip` si la base está vacía,
- arranca la API.

En los logs deben aparecer `[deploy] ...`, `[gtfs] ...` y `[api] Escuchando`.
La primera importación tarda 2–3 minutos y crea ~5.400 viajes; los reinicios posteriores no la repiten.

## 2. Comprobar la API

Copia la URL real del servicio `seatnow-api` desde Render (suele ser
`https://seatnow-api.onrender.com`; si el nombre estaba ocupado, Render añade un sufijo).
Abre en el navegador:

- `https://TU-URL.onrender.com/health` → `{"status":"ok",...}`
- `https://TU-URL.onrender.com/api/trains/stats` → debe mostrar `trips > 0` y `lastGtfsIngestedAt`.

## 3. Conectar Streamlit Cloud

En https://share.streamlit.io abre la app → **Settings → Secrets** y añade:

```toml
API_BASE_URL = "https://TU-URL.onrender.com"
```

Guarda y reinicia la app (**Reboot app**). Si el secreto ya existía, reemplaza el valor.

Con `API_BASE_URL` configurado, la app espera hasta 75 s el arranque en frío de Render.
Si aun así no responde, muestra el badge naranja y el botón **Backend verbinden** para reintentar.

## 4. Iniciar sesión

- Usuario normal: `demo@trenes.local` / `demo1234`
- Admin: `admin@trenes.local` / la contraseña elegida en el paso 1

El botón **Demo-Anmeldung** solo rellena ese usuario; **no** activa el modo demo.
El modo demo únicamente aparece si la API no responde.

## 5. Cómo saber que ya es real

- No se ve el badge naranja **Demo-Modus** ni el aviso **Beispieldaten**.
- Buscar `Fulda → Frankfurt Hbf` devuelve trenes ICE/RE del feed GTFS.
- `/api/trains/stats` muestra `lastGtfsIngestedAt` y el proveedor `db-rest` en `realtime`.
- En Render, los logs muestran `[gtfs]` y `[db] Conectado a PostgreSQL`.

## Limitaciones del plan gratuito

- El web service **duerme** tras ~15 minutos sin tráfico; el primer acceso tarda ~1 minuto.
- PostgreSQL free **caduca a los 30 días** y se borra. Render avisa por correo. Exporta con `pg_dump` o cambia a un plan de pago antes de esa fecha.
- El Key Value free también puede tener límites de memoria; el `maxmemoryPolicy` está en `noeviction`.
- `db-rest` usa el espejo público `v6/v5.db.transport.rest`; si falla, se mantienen los horarios GTFS y el tiempo real queda vacío.

## Actualizaciones

Cada `git push` a `main` redespliega la API (`autoDeploy: true`) y Streamlit recarga la app.
Los cambios de esquema se aplican con `prisma db push` en el arranque; los datos se conservan
salvo que la base se recree.

## Problemas frecuentes

- **Sigue en Demo-Modus**: falta el secreto `API_BASE_URL`, la URL no es la real o Render aún duerme. Pulsa **Backend verbinden**.
- **Error CORS**: `FRONTEND_URL` en Render debe ser exactamente la URL de Streamlit (`https://seatnow-3nx3g79dr4dx5teqsi2fha.streamlit.app`).
- **La API no arranca**: revisa en logs `[env] Variables de entorno invalidas`; `ADMIN_PASSWORD` y `DATABASE_URL` son obligatorias.
- **0 resultados de búsqueda**: el feed filtra por fecha/hora; de noche puede no haber más trenes ese día. Prueba otra hora.
- **La importación GTFS falla**: revisa `GTFS_URL` y vuelve a desplegar con la base vacía.

## Probar la imagen en local (opcional)

```powershell
docker build -t seatnow-api:test backend
docker run --rm -p 3100:3100 `
  -e PORT=3100 `
  -e DATABASE_URL="postgresql://USUARIO:CLAVE@host.docker.internal:5433/seatnow_test?schema=public" `
  -e REDIS_URL="redis://host.docker.internal:6379" `
  -e JWT_SECRET="test-secret-123456" `
  -e ADMIN_PASSWORD="test-admin-1234" `
  -e GTFS_URL="https://download.gtfs.de/germany/fv_free/latest.zip" `
  -e REALTIME_PROVIDER="off" `
  -e FRONTEND_URL="http://localhost:8501" `
  seatnow-api:test
```

Luego inicia Streamlit con `$env:API_BASE_URL="http://localhost:3100"; streamlit run app.py`.

## Seguridad

- No subas `.env`, contraseñas ni claves al repositorio (es público).
- `ADMIN_PASSWORD` se guarda como secreto en Render (`sync: false`).
- `DEMO_PASSWORD` está en `render.yaml` y es pública por diseño; cámbiala si no quieres ese acceso.
- No se necesitan las credenciales `DB_CLIENT_ID` / `DB_API_KEY` de Deutsche Bahn para este despliegue.

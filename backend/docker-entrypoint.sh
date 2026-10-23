#!/bin/sh
set -eu

export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-${RENDER_EXTERNAL_URL:-http://localhost:${PORT:-3000}}}"

echo "[deploy] Sincronizando esquema Prisma"
npx prisma db push --skip-generate

echo "[deploy] Creando admin"
node dist/scripts/seed-admin.js

echo "[deploy] Creando usuario demo"
node dist/scripts/seed-demo.js

TRIPS=$(node -e 'const {PrismaClient}=require("@prisma/client");const p=new PrismaClient();p.trip.count().then((n)=>{process.stdout.write(String(n));return p.$disconnect();}).catch((e)=>{console.error(e);process.exit(1);});')

if [ "$TRIPS" = "0" ]; then
  echo "[deploy] Base vacía: importando GTFS"
  node dist/modules/trains/ingest-gtfs.js
else
  echo "[deploy] Viajes ya importados: $TRIPS"
fi

echo "[deploy] Iniciando API"
exec node dist/server.js

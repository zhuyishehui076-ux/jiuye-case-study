#!/bin/sh
set -eu

echo "[app-entrypoint] preflighting upload namespace without claiming it"
python -m app.services.storage_namespace preflight --kind uploads --path "$UPLOAD_DIR"
echo "[app-entrypoint] preflighting database identity before migrations"
python -m app.services.installation --pre-migration
echo "[app-entrypoint] applying database migrations"
alembic upgrade head
echo "[app-entrypoint] verifying database installation identity"
python -m app.services.installation
echo "[app-entrypoint] claiming or verifying upload namespace ownership"
python -m app.services.storage_namespace ensure --kind uploads --path "$UPLOAD_DIR"
echo "[app-entrypoint] preflight complete; starting API"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log

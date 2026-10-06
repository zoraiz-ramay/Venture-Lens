#!/bin/sh
set -e

# The platform injects user-configured credentials with a USER_ prefix; the app reads the
# unprefixed names. Map them across without overriding anything already set.
for name in GLASSDOLLAR_API_KEY OPENAI_API_KEY GEMINI_API_KEY SIEMENS_DIRECTORY_API_KEY \
            TRACXN_REDIRECT_URI ENTRA_TENANT_ID ENTRA_CLIENT_ID ENTRA_CLIENT_SECRET \
            ENTRA_REDIRECT_URI ADMIN_UPNS; do
  eval "val=\${USER_$name:-}"
  eval "cur=\${$name:-}"
  if [ -n "$val" ] && [ -z "$cur" ]; then
    export "$name=$val"
  fi
done
unset val cur

# Start Redis
echo "Starting Redis..."
redis-server /app/redis.conf --daemonize yes

# Wait for Redis to be ready
until redis-cli ping 2>/dev/null | grep -q PONG; do
  sleep 0.5
done
echo "Redis is ready."

# Start the application
exec gunicorn api.main:app \
  --bind 0.0.0.0:8080 \
  --workers 2 \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout 300 \
  --graceful-timeout 30

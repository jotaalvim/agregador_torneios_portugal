#!/bin/sh
# Idempotent bootstrap: create the GoatCounter site on first run (fresh
# volume), skip it on every later deploy (site already exists on the
# goatcounter_data volume). Exit code of "db show site" is the check:
# 0 = found, 1 = not found (verified against this image's actual behavior).
set -e

if [ -z "$GC_SITE_VHOST" ] || [ -z "$GC_SITE_ADMIN_EMAIL" ] || [ -z "$GC_SITE_ADMIN_PASSWORD" ]; then
  echo "GC_SITE_VHOST/ADMIN_EMAIL/ADMIN_PASSWORD not all set -- skipping" \
       "auto-create, use the web setup wizard instead."
elif ! goatcounter db show site -find="$GC_SITE_VHOST" >/dev/null 2>&1; then
  echo "No site for $GC_SITE_VHOST yet -- creating it."
  goatcounter db create site -createdb \
    -vhost="$GC_SITE_VHOST" \
    -user.email="$GC_SITE_ADMIN_EMAIL" \
    -user.password="$GC_SITE_ADMIN_PASSWORD"
fi

exec goatcounter serve -automigrate -listen :8080 -tls http

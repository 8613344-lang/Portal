#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn portal.wsgi:application --bind 0.0.0.0:8000 --workers 3 --threads 2 --timeout 120 --access-logfile - --error-logfile -

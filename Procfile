release: python manage.py migrate --noinput
web: daphne -b 0.0.0.0 -p $PORT config.asgi:application

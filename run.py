import os

from app import create_app

if __name__ == "__main__" and os.environ.get("APP_ENV") == "production":
    raise SystemExit(
        "Refusing to start the Flask development server in production. "
        "Use Gunicorn: gunicorn --config gunicorn.conf.py wsgi:app"
    )

app = create_app()

if __name__ == "__main__":
    app.run()

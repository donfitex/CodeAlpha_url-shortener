from flask import Flask


def register_blueprints(app: Flask) -> None:
    from app.routes.main import main_bp

    app.register_blueprint(main_bp)
from flask import Flask


def register_blueprints(app: Flask) -> None:
    from app.routes.api import api_bp
    from app.routes.health import health_bp
    from app.routes.main import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(health_bp)

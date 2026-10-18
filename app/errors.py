from flask import Flask, render_template


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(error):
        return render_template("404.html"), 404

    # 400 / 405 / 500 and JSON errors for the API
from flask import Blueprint

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def index():
    return "LinkForge is running"
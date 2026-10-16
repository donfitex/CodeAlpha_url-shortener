import os

from .development import DevelopmentConfig
from .production import ProductionConfig
from .testing import TestingConfig

_configs = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    name = name or os.environ.get("APP_ENV", "development")
    try:
        config = _configs[name]
    except KeyError:
        raise ValueError(
            f"Unknown APP_ENV '{name}'. Choose from: {', '.join(_configs)}"
        ) from None
    if hasattr(config, "validate"):
        config.validate()
    return config
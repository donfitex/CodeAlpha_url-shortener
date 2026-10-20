import os

from app import create_app

# Production unless told otherwise, so a missing APP_ENV can never silently
# start the development configuration.
app = create_app(os.environ.get("APP_ENV", "production"))

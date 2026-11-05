"""Print the server's OpenAPI schema (used to generate admin UI types)."""

import json

from app.main import create_app

print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))

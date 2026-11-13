import ast
import json
from pathlib import Path

Import("env")

project_dir = Path(env.subst("$PROJECT_DIR"))
env_file = project_dir / ".env"
required_keys = (
    "WIFI_SSID",
    "WIFI_PASSWORD",
    "FIREBASE_DATABASE_URL",
    "FIREBASE_LEGACY_TOKEN",
)

if not env_file.is_file():
    raise RuntimeError("Missing .env file. Copy .env.example to .env and fill in its values.")

values = {}
for line_number, line in enumerate(env_file.read_text(encoding="utf-8-sig").splitlines(), 1):
    line = line.strip()
    if not line or line.startswith("#"):
        continue

    key, separator, value = line.partition("=")
    if not separator or not key.strip():
        raise RuntimeError(f"Invalid .env entry on line {line_number}")

    key = key.strip()
    value = value.strip()
    if value.startswith(("'", '"')):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError) as error:
            raise RuntimeError(f"Invalid quoted value for {key} in .env") from error
        if not isinstance(value, str):
            raise RuntimeError(f"Value for {key} in .env must be a string")
    values[key] = value

missing = [key for key in required_keys if not values.get(key)]
if missing:
    raise RuntimeError("Missing values in .env: " + ", ".join(missing))

generated_header = project_dir / ".pio" / "generated" / "env_secrets.h"
generated_header.parent.mkdir(parents=True, exist_ok=True)
header_lines = ["#pragma once"]
header_lines.extend(f"#define {key} {json.dumps(values[key])}" for key in required_keys)
generated_header.write_text("\n".join(header_lines) + "\n", encoding="utf-8")
env.Append(CPPPATH=[str(generated_header.parent)])
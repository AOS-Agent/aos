"""Schema store for reusable extraction patterns."""

import json
from pathlib import Path
from typing import Optional

import yaml

# Instance schemas (user-generated, accumulate over time)
INSTANCE_DIR = Path.home() / ".aos" / "data" / "crawler" / "schemas"

# Seed schemas (shipped with framework, copied on first run)
SEED_DIR = Path(__file__).parent / "seed-schemas"


class SchemaStore:
    """Manages CSS/XPath extraction schemas."""

    def __init__(self):
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
        self._seed_if_empty()

    def _seed_if_empty(self):
        """Copy seed schemas to instance if the store is empty."""
        if any(INSTANCE_DIR.glob("*.yaml")):
            return
        if not SEED_DIR.exists():
            return
        for src in SEED_DIR.glob("*.yaml"):
            dst = INSTANCE_DIR / src.name
            if not dst.exists():
                dst.write_text(src.read_text())

    def get(self, name: str) -> Optional[dict]:
        """Load a schema by name."""
        path = INSTANCE_DIR / f"{name}.yaml"
        if not path.exists():
            # Try without extension normalization
            for f in INSTANCE_DIR.glob("*.yaml"):
                schema = yaml.safe_load(f.read_text())
                if schema and schema.get("name") == name:
                    return schema
            return None
        return yaml.safe_load(path.read_text())

    def save(self, schema: dict) -> str:
        """Save a schema. Returns the filename."""
        name = schema.get("name", "unnamed")
        filename = f"{name}.yaml"
        path = INSTANCE_DIR / filename
        path.write_text(yaml.dump(schema, default_flow_style=False, sort_keys=False))
        return filename

    def list_all(self) -> list[dict]:
        """List all schemas with summary info."""
        schemas = []
        for f in sorted(INSTANCE_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text())
                if data:
                    schemas.append({
                        "name": data.get("name", f.stem),
                        "domain": data.get("domain", ""),
                        "description": data.get("description", ""),
                        "strategy": data.get("strategy", "css"),
                    })
            except Exception:
                continue
        return schemas

    def delete(self, name: str) -> bool:
        """Delete a schema by name."""
        path = INSTANCE_DIR / f"{name}.yaml"
        if path.exists():
            path.unlink()
            return True
        return False

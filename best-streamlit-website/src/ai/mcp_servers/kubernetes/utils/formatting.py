from __future__ import annotations

import json
from typing import Any


def to_json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def to_yaml_text(value: Any) -> str:
    # Optional dependency; we keep it isolated here.
    import yaml  # type: ignore

    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)

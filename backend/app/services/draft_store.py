import json
from pathlib import Path

from ..schemas.draft import VideoPlanDraft

_STORE = Path(__file__).parent.parent.parent / "data" / "latest_draft.json"


def save_draft(draft: VideoPlanDraft) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(draft.model_dump_json(indent=2), encoding="utf-8")


def get_latest_draft() -> VideoPlanDraft | None:
    if not _STORE.exists():
        return None
    data = json.loads(_STORE.read_text(encoding="utf-8"))
    return VideoPlanDraft(**data)

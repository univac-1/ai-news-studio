import json
from datetime import datetime, timezone
from pathlib import Path

_STORE = Path(__file__).parent.parent.parent / "data" / "used_news.json"


def _load() -> dict[str, str]:
    if not _STORE.exists():
        return {}
    return json.loads(_STORE.read_text(encoding="utf-8"))


def _save(store: dict[str, str]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def get_used_news() -> list[dict]:
    store = _load()
    return [{"id": k, "used_at": v} for k, v in store.items()]


def get_used_ids() -> set[str]:
    return set(_load().keys())


def mark_as_used(news_id: str) -> None:
    store = _load()
    if news_id not in store:
        store[news_id] = datetime.now(timezone.utc).isoformat()
        _save(store)

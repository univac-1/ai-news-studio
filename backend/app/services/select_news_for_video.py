import json
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings
from ..schemas.news import NewsItem

MAX_ITEMS = 7

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+-]*")
_SPACE_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://\S+")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]+")
_MODEL_VERSION_RE = re.compile(
    r"\b(?:"
    r"gpt|claude|gemini|llama|mistral|qwen|deepseek|grok|command|nova|phi|o"
    r")[a-z0-9.+-]*\d[a-z0-9.+-]*\b"
)

_STOP_TOKENS = {
    "about",
    "after",
    "and",
    "announces",
    "announced",
    "available",
    "based",
    "brings",
    "cost",
    "costs",
    "efficiency",
    "efficient",
    "family",
    "families",
    "for",
    "frontier",
    "from",
    "generative",
    "has",
    "how",
    "improves",
    "improved",
    "improvement",
    "improvements",
    "intelligence",
    "into",
    "its",
    "launch",
    "launches",
    "latest",
    "llm",
    "llms",
    "model",
    "models",
    "new",
    "news",
    "now",
    "of",
    "on",
    "open",
    "performance",
    "price",
    "pricing",
    "release",
    "released",
    "says",
    "the",
    "to",
    "unveils",
    "use",
    "using",
    "with",
}


def _canonical_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url.strip())
    if not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _URL_RE.sub(" ", text)
    text = re.sub(r"[_/|:;,.!?()[\]{}\"'`~]+", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _normalize_model_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _URL_RE.sub(" ", text)
    text = re.sub(r"[_/|:;,!?()[\]{}\"'`~]+", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _item_text(item: NewsItem) -> str:
    return " ".join(
        [
            item.title,
            item.summary,
            item.impact,
            item.action,
            item.provider,
            item.model_name,
            item.model_type,
            " ".join(item.tags),
        ]
    )


def _tokens(text: str) -> set[str]:
    normalized = _normalize_text(text)
    return {
        token
        for token in _TOKEN_RE.findall(normalized)
        if token not in _STOP_TOKENS and (len(token) >= 3 or any(ch.isdigit() for ch in token))
    }


def _high_signal_text(item: NewsItem) -> str:
    return " ".join([item.title, item.summary, item.provider, item.model_name])


def _known_value(value: str) -> bool:
    return bool(value and value.strip() and value.strip().lower() != "unknown")


def _model_mentions(item: NewsItem) -> set[str]:
    text = _normalize_model_text(_high_signal_text(item))
    mentions = set(_MODEL_VERSION_RE.findall(text))

    if _known_value(item.model_name):
        normalized_model = _normalize_model_text(item.model_name)
        model_mentions = set(_MODEL_VERSION_RE.findall(normalized_model))
        if model_mentions:
            mentions.update(model_mentions)
        else:
            mentions.update(_tokens(item.model_name))

    # Split forms such as "gpt 5.6" are not covered by the token regex above.
    split_versions = re.findall(
        r"\b(gpt|claude|gemini|llama|mistral|qwen|deepseek|grok|o)\s+(\d[\w.+-]*)\b",
        text,
    )
    mentions.update(f"{name}-{version}" for name, version in split_versions)
    return {mention for mention in mentions if mention and mention not in _STOP_TOKENS}


def _cjk_ngrams(text: str, size: int = 4) -> set[str]:
    normalized = _normalize_text(text)
    chunks = _CJK_RE.findall(normalized)
    compact = "".join(chunks)
    if len(compact) < size:
        return {compact} if compact else set()
    return {compact[i : i + size] for i in range(len(compact) - size + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _has_same_model_story(left: NewsItem, right: NewsItem) -> bool:
    left_mentions = _model_mentions(left)
    right_mentions = _model_mentions(right)
    shared_mentions = left_mentions & right_mentions
    if not shared_mentions:
        return False

    same_provider = (
        _known_value(left.provider)
        and _known_value(right.provider)
        and _normalize_text(left.provider) == _normalize_text(right.provider)
    )

    left_tokens = _tokens(_high_signal_text(left))
    right_tokens = _tokens(_high_signal_text(right))
    shared_tokens = left_tokens & right_tokens
    token_similarity = _jaccard(left_tokens, right_tokens)

    if same_provider and (token_similarity >= 0.16 or len(shared_tokens) >= 2):
        return True
    return token_similarity >= 0.24 and len(shared_tokens) >= 3


def _looks_like_same_story(left: NewsItem, right: NewsItem) -> bool:
    left_url = _canonical_url(left.url)
    right_url = _canonical_url(right.url)
    if left_url and left_url == right_url:
        return True

    if _has_same_model_story(left, right):
        return True

    left_mentions = _model_mentions(left)
    right_mentions = _model_mentions(right)
    if left_mentions and right_mentions and not (left_mentions & right_mentions):
        return False

    left_text = _item_text(left)
    right_text = _item_text(right)
    left_tokens = _tokens(left_text)
    right_tokens = _tokens(right_text)
    shared_tokens = left_tokens & right_tokens
    token_similarity = _jaccard(left_tokens, right_tokens)

    if token_similarity >= 0.56:
        return True
    if token_similarity >= 0.34 and len(shared_tokens) >= 4:
        return True

    left_ngrams = _cjk_ngrams(left_text)
    right_ngrams = _cjk_ngrams(right_text)
    return _jaccard(left_ngrams, right_ngrams) >= 0.62


def dedupe_similar_news(items: list[NewsItem]) -> list[NewsItem]:
    deduped: list[NewsItem] = []
    for item in items:
        if any(_looks_like_same_story(item, existing) for existing in deduped):
            continue
        deduped.append(item)
    return deduped


async def select_news_for_video(items: list[NewsItem]) -> list[NewsItem]:
    items = dedupe_similar_news(items)

    if len(items) <= MAX_ITEMS:
        return items

    if not settings.GEMINI_PROJECT:
        return items[:MAX_ITEMS]

    vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
    model = GenerativeModel("gemini-2.5-flash")

    items_text = "\n".join(
        f"ID:{item.id} | {item.title} | インパクト:{item.impact}"
        for item in items
    )

    prompt = (
        f"以下のAIニュース一覧から、YouTube動画（約10分）に最適な{MAX_ITEMS}件を選んでください。\n"
        "選定基準：\n"
        "- 読者への影響度が高い\n"
        "- モデルリリース・研究・ビジネス・規制など多様なトピック\n"
        "- 類似・重複するニュースは最も重要な1件に絞る\n\n"
        f"ニュース一覧：\n{items_text}\n\n"
        f'選んだニュースのIDだけを JSON 配列で返してください。例: ["id1","id2"]\n'
        "説明は不要です。JSONのみ返してください。"
    )

    response = await model.generate_content_async(prompt)
    text = response.text.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)

    selected_ids: list[str] = json.loads(text.strip())

    id_to_item = {item.id: item for item in items}
    result = [id_to_item[sid] for sid in selected_ids if sid in id_to_item]
    return dedupe_similar_news(result) if result else items[:MAX_ITEMS]

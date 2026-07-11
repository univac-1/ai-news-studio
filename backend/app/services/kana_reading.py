import json
import re

import alkana
import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings

# AI関連の固有名詞はalkanaの一般英語辞書に無い・読みが特殊なため、優先辞書で変換する。
# 複数語エントリを含むため、長いキーから先にマッチさせる。
CUSTOM_READINGS: dict[str, str] = {
    "AI専門家": "エーアイせんもんか",
    "Google DeepMind": "グーグルディープマインド",
    "Hugging Face": "ハギングフェイス",
    "Stability AI": "スタビリティエーアイ",
    "Vertex AI": "バーテックスエーアイ",
    "ChatGPT": "チャットジーピーティー",
    "GPT-Live": "ジーピーティーライブ",
    "GPT-4o": "ジーピーティーフォーオー",
    "Anthropic": "アンソロピック",
    "DeepSeek": "ディープシーク",
    "DeepMind": "ディープマインド",
    "Copilot": "コパイロット",
    "GitHub": "ギットハブ",
    "Gemini": "ジェミニ",
    "Claude": "クロード",
    "OpenAI": "オープンエーアイ",
    "NVIDIA": "エヌビディア",
    "Mistral": "ミストラル",
    "Llama": "ラマ",
    "Qwen": "クウェン",
    "Grok": "グロック",
    "xAI": "エックスエーアイ",
    "Azure": "アジュール",
    "AWS": "エーダブリューエス",
    "AGI": "エージーアイ",
    "API": "エーピーアイ",
    "GPT": "ジーピーティー",
    "GPU": "ジーピーユー",
    "LLM": "エルエルエム",
    "RAG": "ラグ",
    "SDK": "エスディーケー",
    "CLI": "シーエルアイ",
    "MCP": "エムシーピー",
    "JSON": "ジェイソン",
    "AI": "エーアイ",
}

_ALPHABET_READINGS = {
    "a": "エー", "b": "ビー", "c": "シー", "d": "ディー", "e": "イー",
    "f": "エフ", "g": "ジー", "h": "エイチ", "i": "アイ", "j": "ジェー",
    "k": "ケー", "l": "エル", "m": "エム", "n": "エヌ", "o": "オー",
    "p": "ピー", "q": "キュー", "r": "アール", "s": "エス", "t": "ティー",
    "u": "ユー", "v": "ブイ", "w": "ダブリュー", "x": "エックス",
    "y": "ワイ", "z": "ゼット",
}

_CUSTOM_READINGS_LOWER = {key.lower(): value for key, value in CUSTOM_READINGS.items()}
_CUSTOM_RE = re.compile(
    "(?<![A-Za-z0-9])(?:"
    + "|".join(re.escape(key) for key in sorted(CUSTOM_READINGS, key=len, reverse=True))
    + ")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# 「GPT-4o」「models.json」のようにピリオド・ハイフンでつながる表記を1トークンとして扱う
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*")
_PART_SPLIT_RE = re.compile(r"([.\-])")


def _spell_out(word: str) -> str:
    return "".join(_ALPHABET_READINGS.get(char.lower(), char) for char in word)


def _alkana_reading(word: str) -> str | None:
    return alkana.get_kana(word) or alkana.get_kana(word.lower())


def _part_reading(part: str, reading_map: dict[str, str]) -> str:
    if not part or not part[0].isalpha():
        return part
    kana = _alkana_reading(part)
    if kana:
        return kana
    mapped = reading_map.get(part.lower())
    if mapped:
        return mapped
    return _spell_out(part)


def to_voice_text(text: str, reading_map: dict[str, str] | None = None) -> str:
    """音声合成用に英単語をカタカナ読みへ変換する。字幕・表示用の原文には使わないこと。"""
    readings = reading_map or {}
    replaced = _CUSTOM_RE.sub(
        lambda match: _CUSTOM_READINGS_LOWER[match.group(0).lower()], text
    )

    def _replace_token(match: re.Match[str]) -> str:
        parts = _PART_SPLIT_RE.split(match.group(0))
        return "".join(
            part if part in {".", "-"} else _part_reading(part, readings)
            for part in parts
        )

    return _TOKEN_RE.sub(_replace_token, replaced)


def _collect_unknown_words(texts: list[str]) -> set[str]:
    unknown: set[str] = set()
    for text in texts:
        stripped = _CUSTOM_RE.sub(" ", text)
        for match in _TOKEN_RE.finditer(stripped):
            for part in _PART_SPLIT_RE.split(match.group(0)):
                if part in {".", "-"} or not part or not part[0].isalpha():
                    continue
                if _alkana_reading(part):
                    continue
                unknown.add(part)
    return unknown


async def build_reading_map(texts: list[str]) -> dict[str, str]:
    """カスタム辞書にもalkanaにも無い英単語の読みをGeminiに一括で問い合わせる。

    GEMINI_PROJECT未設定・失敗時は空dictを返し、to_voice_text側のスペルアウトに委ねる。
    """
    unknown = _collect_unknown_words(texts)
    if not unknown or not settings.GEMINI_PROJECT:
        return {}

    try:
        vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
        model = GenerativeModel("gemini-2.5-flash")

        words = "\n".join(sorted(unknown))
        prompt = (
            "以下の英単語それぞれについて、日本語の音声合成で読み上げるためのカタカナ読みを返してください。\n"
            "製品名・企業名・技術用語は日本のIT業界で一般的な呼び方を優先してください。\n"
            '出力はJSONのみ: {"単語": "カタカナ読み", ...}\n'
            "説明は不要です。JSONのみ返してください。\n\n"
            f"単語一覧:\n{words}"
        )

        response = await model.generate_content_async(prompt)
        text = response.text.strip()
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        result = json.loads(text.strip())

        return {
            str(key).lower(): str(value).strip()
            for key, value in result.items()
            if isinstance(value, str) and value.strip()
        }
    except Exception:
        return {}

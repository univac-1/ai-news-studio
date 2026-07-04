import re
from dataclasses import dataclass

from ..schemas.news import NewsItem


@dataclass(frozen=True)
class CategoryStyle:
    label: str
    color: str
    icon: str  # video_generator 側で描画する簡易アイコンの種別


# スライド・区切り画面で使うカテゴリごとの表示スタイル
CATEGORY_STYLES: dict[str, CategoryStyle] = {
    "security": CategoryStyle(label="セキュリティ", color="#dc2626", icon="shield"),
    "media": CategoryStyle(label="生成メディア", color="#ec4899", icon="spark"),
    "openai": CategoryStyle(label="モデル", color="#10a37f", icon="spark"),
    "anthropic": CategoryStyle(label="モデル", color="#d97706", icon="spark"),
    "google": CategoryStyle(label="モデル", color="#4285f4", icon="spark"),
    "aws": CategoryStyle(label="クラウド", color="#ff9900", icon="cloud"),
    "hardware": CategoryStyle(label="チップ", color="#76b900", icon="chip"),
    "devtools": CategoryStyle(label="開発者ツール", color="#8b5cf6", icon="wrench"),
    "business": CategoryStyle(label="ビジネス", color="#475569", icon="building"),
    "general": CategoryStyle(label="AI動向", color="#f59e0b", icon="spark"),
}

# 判定は上から順に評価する（セキュリティ・メディア・開発ツールはプロバイダーより優先）
_KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "security",
        (
            "セキュリティ", "脆弱性", "攻撃", "サイバー", "マルウェア", "フィッシング",
            "security", "vulnerability", "exploit", "cyber", "malware", "phishing",
            "attack", "シャドーai", "shadow ai",
        ),
    ),
    (
        "media",
        (
            "nano banana", "imagen", "veo", "sora", "midjourney", "画像生成", "動画生成",
            "text-to-image", "text-to-video", "image generation", "video generation",
        ),
    ),
    (
        "devtools",
        (
            "github", "copilot", "codex", "cli", "ide", "vscode", "エディタ",
            "developer tool", "開発者ツール", "コマンド", "sdk", "api",
        ),
    ),
    (
        "aws",
        ("aws", "amazon", "bedrock", "sagemaker", "azure", "クラウド", "cloud run"),
    ),
    (
        "hardware",
        (
            "nvidia", "gpu", "チップ", "半導体", "broadcom", "tensorrt", "blackwell",
            "chip", "inference chip", "データセンター", "冷却", "電力",
        ),
    ),
    (
        "business",
        (
            "規制", "政府", "政権", "法", "訴訟", "著作権", "投資", "支出", "gartner",
            "導入", "企業", "regulation", "government", "lawsuit", "liability",
            "white house", "policy",
        ),
    ),
]

_PROVIDER_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("openai", ("openai", "gpt")),
    ("anthropic", ("anthropic", "claude")),
    ("google", ("google", "deepmind", "gemini")),
    ("aws", ("amazon", "aws")),
    ("hardware", ("nvidia", "broadcom")),
]


def _keyword_hit(keyword: str, haystack: str) -> bool:
    """短い英字キーワードの誤爆("cli" が "client" に一致等)を避けるための判定。

    ASCII のみのキーワードは単語境界マッチ、日本語を含むキーワードは
    従来どおり部分文字列マッチにする。
    """
    if keyword.isascii():
        return re.search(rf"\b{re.escape(keyword)}\b", haystack) is not None
    return keyword in haystack


def _any_keyword_hit(keywords: tuple[str, ...], haystack: str) -> bool:
    return any(_keyword_hit(keyword, haystack) for keyword in keywords)


def categorize_text(text: str, provider: str = "") -> str:
    haystack = text.lower()
    provider_haystack = provider.lower() if provider else haystack

    # 1. セキュリティは内容判定を最優先（提供元がどこでも攻撃/脆弱性の話はセキュリティ扱い）
    if _any_keyword_hit(_KEYWORD_RULES[0][1], haystack):
        return "security"

    # 2. 生成メディア(画像・動画生成)
    if _any_keyword_hit(_KEYWORD_RULES[1][1], haystack):
        return "media"

    # 3. 開発者ツール
    if _any_keyword_hit(_KEYWORD_RULES[2][1], haystack):
        return "devtools"

    # 4. AWS / クラウド
    if _any_keyword_hit(_KEYWORD_RULES[3][1], haystack):
        return "aws"

    # 5. ハードウェア
    if _any_keyword_hit(_KEYWORD_RULES[4][1], haystack):
        return "hardware"

    # 6. provider ルール(provider引数があればそれに対して、なければ text に対して判定)
    for category, keywords in _PROVIDER_RULES:
        if _any_keyword_hit(keywords, provider_haystack):
            return category

    # 7. ビジネス / 規制
    if _any_keyword_hit(_KEYWORD_RULES[5][1], haystack):
        return "business"

    # 8. それ以外は一般的なAI動向
    return "general"


def categorize_news(item: NewsItem) -> str:
    text = " ".join([item.title, item.provider, item.model_type, *item.tags])
    return categorize_text(text, item.provider)


def category_style(category: str) -> CategoryStyle:
    return CATEGORY_STYLES.get(category, CATEGORY_STYLES["general"])

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
    "openai": CategoryStyle(label="OpenAI", color="#10a37f", icon="spark"),
    "anthropic": CategoryStyle(label="Anthropic", color="#d97706", icon="spark"),
    "google": CategoryStyle(label="Google", color="#4285f4", icon="spark"),
    "aws": CategoryStyle(label="AWS / クラウド", color="#ff9900", icon="cloud"),
    "hardware": CategoryStyle(label="ハードウェア", color="#76b900", icon="chip"),
    "devtools": CategoryStyle(label="開発ツール", color="#8b5cf6", icon="wrench"),
    "business": CategoryStyle(label="ビジネス / 規制", color="#475569", icon="building"),
    "general": CategoryStyle(label="AI動向", color="#f59e0b", icon="spark"),
}

# 判定は上から順に評価する（セキュリティはプロバイダーより優先）
_KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "security",
        (
            "セキュリティ", "脆弱性", "攻撃", "サイバー", "マルウェア", "フィッシング",
            "security", "vulnerability", "exploit", "cyber", "malware", "phishing",
            "シャドーai", "shadow ai",
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
        "hardware",
        (
            "nvidia", "gpu", "チップ", "半導体", "broadcom", "tensorrt", "blackwell",
            "chip", "inference chip", "データセンター", "冷却", "電力",
        ),
    ),
    (
        "aws",
        ("aws", "amazon", "bedrock", "sagemaker", "azure", "クラウド", "cloud run"),
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
    ("openai", ("openai",)),
    ("anthropic", ("anthropic",)),
    ("google", ("google", "deepmind")),
    ("aws", ("amazon", "aws")),
    ("hardware", ("nvidia", "broadcom")),
]


def categorize_news(item: NewsItem) -> str:
    haystack = " ".join(
        [item.title, item.provider, item.model_type, *item.tags]
    ).lower()
    provider = item.provider.lower()

    # セキュリティは内容判定を最優先（提供元がどこでも攻撃/脆弱性の話はセキュリティ扱い）
    security_keywords = _KEYWORD_RULES[0][1]
    if any(keyword in haystack for keyword in security_keywords):
        return "security"

    for category, keywords in _PROVIDER_RULES:
        if any(keyword in provider for keyword in keywords):
            return category

    for category, keywords in _KEYWORD_RULES[1:]:
        if any(keyword in haystack for keyword in keywords):
            return category

    return "general"


def category_style(category: str) -> CategoryStyle:
    return CATEGORY_STYLES.get(category, CATEGORY_STYLES["general"])

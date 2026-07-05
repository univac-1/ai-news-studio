import re
from datetime import datetime, timezone

from ..schemas.draft import VideoPlanDraft, VideoSegment
from ..schemas.news import NewsItem
from .categorize import categorize_news

# VOICEVOX speed1.1 ≈ 8〜8.5字/秒。フック+オープニング+区切り1.8秒で
# 本編#1開始を20秒以内に収めるための文字数バジェット
HOOK_MAX_CHARS = 40
OPENING_MAX_CHARS = 95
TITLE_JA_MAX_CHARS = 16
# セグメント本文(1行要約・Impact・Actionボックス)のフォント自動縮小に頼りすぎないための文字数バジェット
SUMMARY_MAX_CHARS = 55
IMPACT_MAX_CHARS = 40
ACTION_MAX_CHARS = 40

_JAPANESE_RE = re.compile(r"[぀-ヿ一-鿿]")


def contains_japanese(text: str) -> bool:
    return bool(_JAPANESE_RE.search(text))


def shorten(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def template_hook(title_ja: str) -> str:
    return f"{title_ja}。今週最大のAIニュースです。"


def template_intro(count: int) -> str:
    return (
        f"この動画では、今週の重要AIニュース{count}本を短時間でまとめて把握できます。"
        "ラインナップはご覧のとおりです。それでは1本目からいきましょう。"
    )


def template_intro_line(number: int, title_ja: str) -> str:
    return f"{number}本目は、{title_ja}なのだ！"


_REACTION_TEMPLATES = (
    "これは要チェックなのだ！",
    "目が離せない話なのだ！",
    "ボクも気になるのだ！",
    "早く試してみたいのだ！",
)


def template_reaction_line(number: int) -> str:
    return _REACTION_TEMPLATES[(number - 1) % len(_REACTION_TEMPLATES)]


def fallback_title_ja(item: NewsItem) -> str:
    """スライド表示用タイトルのフォールバック。

    Gemini が使えない場合に使う。モデル名が分かればそれを軸に、
    なければ元タイトルを省略せずに使い、描画側の折り返しと縮小で収める。
    """
    model_name = item.model_name if item.model_name not in ("Unknown", "") else ""
    if model_name and len(model_name) <= TITLE_JA_MAX_CHARS:
        return model_name
    return item.title.strip()


def generate_weekly_video_plan(items: list[NewsItem]) -> VideoPlanDraft:
    week_label = ""

    seen: set[str] = set()
    top_providers: list[str] = []
    for item in items[:3]:
        p = item.provider
        if p and p not in ("Unknown", "") and p not in seen:
            top_providers.append(p)
            seen.add(p)
            if len(top_providers) >= 2:
                break
    providers_str = "、".join(top_providers)

    # サムネ文言は「メイン\nサブ」の2行構成(polish_narration の候補と同じ形式)。
    # 1行目がサムネ最大サイズの見出し、2行目が補足サブコピーになる。
    if providers_str:
        title = f"今週のAIニュース速報 — {providers_str} など注目アップデート"
        thumbnail_text = f"今週のAI速報\n{providers_str}など注目{len(items)}本"
    else:
        title = "今週のAIニュース速報 — 重要AI動向まとめ"
        thumbnail_text = f"今週のAI速報\n重要{len(items)}本まとめ"

    # オープニング(5〜20秒想定)。価値提示→ラインナップ提示→本編へ。95字以内。
    intro = template_intro(len(items))

    segments: list[VideoSegment] = []
    for i, item in enumerate(items, 1):
        title_ja = fallback_title_ja(item)
        intro_line = template_intro_line(i, title_ja)
        reaction_line = template_reaction_line(i)
        narration = (
            f"{item.summary}\n"
            f"ポイントは、{item.impact}\n"
            f"次のアクションとしては、{item.action}"
        )
        # ランキングの理由行は1行(約40字)まで描けるため、切り詰めは保険程度にとどめる
        rank_reason = shorten(item.impact.split("。")[0], 40) if item.impact else ""
        segments.append(
            VideoSegment(
                number=i,
                headline=item.title,
                summary=item.summary,
                impact=item.impact,
                action=item.action,
                slide_title=f"#{i} {title_ja}",
                narration=narration,
                intro_line=intro_line,
                reaction_line=reaction_line,
                source=item.source,
                title_ja=title_ja,
                category=categorize_news(item),
                rank_reason=rank_reason,
            )
        )

    # まとめは重要度ランキング形式(順位=選定順)。各順位に短い理由を添える
    top3 = segments[:3]
    ranking_reasons = "".join(
        f"第{seg.number}位は、{seg.title_ja}。{seg.rank_reason}。" for seg in top3
    )
    outro = (
        f"今週の重要度ランキングです。{ranking_reasons}"
        "気になるニュースはチャプターから見返してください。"
        "来週も最新情報をまとめてお届けしますので、チャンネル登録・通知オンをお忘れなく！"
    )

    # フック(0〜5秒想定)。40字以内で今週最大のニュースを一言。
    hook = template_hook(segments[0].title_ja) if segments else ""

    slide_outline = [
        "[フック] 今週最大のニュースを一言",
        "[オープニング] 動画の価値 + ラインナップ一覧",
        *[f"{s.slide_title}（番号付き区切り→本編）" for s in segments],
        "[まとめ] 今週の重要度ランキング + チャンネル登録",
    ]

    narration_script = f"【フック】\n{hook}\n\n【オープニング】\n{intro}\n\n"
    for seg in segments:
        narration_script += f"{seg.narration}\n\n"
    narration_script += f"【まとめ】\n{outro}"

    news_list_lines: list[str] = []
    for seg, item in zip(segments, items):
        if seg.title_ja and seg.title_ja.rstrip("…") not in item.title:
            news_list_lines.append(f"・{seg.title_ja}｜{item.title}（{item.source}）")
        else:
            news_list_lines.append(f"・{item.title}（{item.source}）")
    news_list_str = "\n".join(news_list_lines)
    description = (
        "【今週のAIニュース速報】\n\n"
        f"今週の重要AIニュース{len(items)}本を短時間でまとめて把握できます。\n\n"
        f"▼ 今週取り上げたニュース\n{news_list_str}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📌 チャンネル登録・通知オンで最新情報をキャッチ！\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "音声：VOICEVOX:ずんだもん"
    )

    hashtags = ["#AIニュース", "#AI速報", "#人工知能", "#機械学習"]
    for p in top_providers:
        hashtags.append(f"#{p}")
    tag_seen: set[str] = set(hashtags)
    for item in items:
        for tag in item.tags:
            candidate = f"#{tag.replace(' ', '')}"
            if candidate not in tag_seen and len(hashtags) < 10:
                hashtags.append(candidate)
                tag_seen.add(candidate)

    reference_urls = [item.url for item in items if item.url]

    return VideoPlanDraft(
        title=title,
        title_candidates=[title],
        week_label=week_label,
        thumbnail_text=thumbnail_text,
        thumbnail_text_candidates=[thumbnail_text],
        hook=hook,
        intro=intro,
        segments=segments,
        outro=outro,
        slide_outline=slide_outline,
        narration_script=narration_script,
        description=description,
        hashtags=hashtags,
        reference_urls=reference_urls,
        total_items=len(items),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

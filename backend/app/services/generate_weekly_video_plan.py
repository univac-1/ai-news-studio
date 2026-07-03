from datetime import datetime, timezone

from ..schemas.draft import VideoPlanDraft, VideoSegment
from ..schemas.news import NewsItem


def _week_range_label(items: list[NewsItem]) -> str:
    if not items:
        return ""
    dts = []
    for item in items:
        try:
            dts.append(datetime.fromisoformat(item.published_at.replace("Z", "+00:00")))
        except (ValueError, AttributeError):
            pass
    if not dts:
        return ""
    lo, hi = min(dts), max(dts)
    return f"{lo.month}月{lo.day}日〜{hi.month}月{hi.day}日"


def generate_weekly_video_plan(items: list[NewsItem]) -> VideoPlanDraft:
    week_label = _week_range_label(items)

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

    if providers_str:
        title = f"今週のAIニュース速報（{week_label}）— {providers_str} など注目アップデート"
        thumbnail_text = f"今週のAI速報\n{providers_str} など\n注目{len(items)}本"
    else:
        title = f"今週のAIニュース速報（{week_label}）— 重要AI動向まとめ"
        thumbnail_text = f"今週のAI速報\n重要{len(items)}本まとめ"

    intro = (
        f"今週は重要度Aのニュースが {len(items)} 件ありました。"
        + (f"{providers_str} を中心に、" if providers_str else "")
        + "AI業界の最新動向を一気にお届けします。"
    )

    segments: list[VideoSegment] = []
    for i, item in enumerate(items, 1):
        model_prefix = (
            f"[{item.model_name}] "
            if item.model_name and item.model_name not in ("Unknown", "")
            else ""
        )
        narration = (
            f"#{i}: {item.title}。\n"
            f"{item.summary}\n"
            f"インパクト: {item.impact}\n"
            f"あなたへのアクション: {item.action}"
        )
        segments.append(
            VideoSegment(
                number=i,
                headline=item.title,
                summary=item.summary,
                impact=item.impact,
                action=item.action,
                slide_title=f"#{i} {model_prefix}{item.title}",
                narration=narration,
                source=item.source,
            )
        )

    outro = (
        "以上、今週のAI重要ニュースをお届けしました。"
        "来週も最新情報をまとめてお届けしますので、チャンネル登録・通知オンをお忘れなく！"
    )

    slide_outline = [
        f"[表紙] {title}",
        "[イントロ] 今週のハイライト",
        *[s.slide_title for s in segments],
        "[アウトロ] チャンネル登録・来週の予告",
    ]

    narration_script = f"【イントロ】\n{intro}\n\n"
    for seg in segments:
        narration_script += f"{seg.narration}\n\n"
    narration_script += f"【アウトロ】\n{outro}"

    news_list_str = "\n".join(
        f"・{item.title}（{item.source}）" for item in items
    )
    description = (
        f"【今週のAIニュース速報 {week_label}】\n\n"
        f"{intro}\n\n"
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
        week_label=week_label,
        thumbnail_text=thumbnail_text,
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

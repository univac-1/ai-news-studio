from app.schemas.news import NewsItem
from app.services.generate_weekly_video_plan import (
    OUTRO_MAX_CHARS,
    RANK_REASON_MAX_CHARS,
    generate_weekly_video_plan,
)


def test_rank_reason_is_kept_concise():
    impact = "視聴者の判断に必要な情報が多く、導入判断への影響が大きい。追加説明。"
    item = NewsItem(
        id="news-1",
        title="新しいAIモデルが公開",
        source="Example",
        summary="新しいAIモデルが公開された。",
        impact=impact,
        action="仕様を確認する。",
        importance="A",
    )

    draft = generate_weekly_video_plan([item])

    reason = draft.segments[0].rank_reason
    assert len(reason) <= RANK_REASON_MAX_CHARS
    assert reason != impact.split("。")[0]
    assert len(draft.outro) <= OUTRO_MAX_CHARS

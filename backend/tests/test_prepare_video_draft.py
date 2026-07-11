import pytest

from app.schemas.draft import VideoPlanDraft, VideoSegment
from app.services.prepare_video_draft import prepare_draft_for_video
from app.services.polish_narration import sanitize_spoken_speaker_labels


def _draft_with_speaker_label() -> VideoPlanDraft:
    segment = VideoSegment(
        number=1,
        headline="新しいAIモデルが公開",
        summary="新しいAIモデルが公開された。",
        impact="導入判断への影響が大きい。",
        action="仕様を確認する。",
        slide_title="#1 新しいAIモデル",
        narration="AI専門家が詳しく解説します。これは重要な更新です。",
        intro_line="次にAI専門家が詳しく解説するのだ！",
        reaction_line="AI専門家の解説でよく分かったのだ！",
        source="Example",
        title_ja="新しいAIモデル",
        category="general",
        rank_reason="導入判断に影響",
    )
    return VideoPlanDraft(
        title="今週のAIニュース",
        week_label="2026年7月第2週",
        thumbnail_text="AI速報",
        hook="AI専門家が注目したニュースです。",
        intro="ラインナップはご覧のとおりです。",
        segments=[segment],
        outro="AI専門家のまとめです。登録もお願いします。",
        slide_outline=[],
        narration_script="AI専門家が詳しく解説します。",
        description="説明文",
        hashtags=["#AIニュース"],
        reference_urls=[],
        total_items=1,
        generated_at="2026-07-10T08:00:00+09:00",
    )


def test_sanitize_spoken_speaker_labels_removes_internal_ai_expert_label():
    assert sanitize_spoken_speaker_labels("次にAI専門家が詳しく解説します。") == "次に詳しく解説します。"
    assert sanitize_spoken_speaker_labels("AI専門家の解説です。") == "解説です。"


@pytest.mark.asyncio
async def test_prepare_draft_removes_ai_expert_label_from_spoken_text(monkeypatch):
    from app.services import prepare_video_draft

    monkeypatch.setattr(prepare_video_draft.settings, "GEMINI_PROJECT", "")

    draft = await prepare_draft_for_video(_draft_with_speaker_label())
    spoken_texts = [
        draft.hook,
        draft.intro,
        draft.outro,
        draft.narration_script,
        draft.segments[0].narration,
        draft.segments[0].intro_line,
        draft.segments[0].reaction_line,
    ]

    assert all("AI専門家" not in text for text in spoken_texts)
    assert "詳しく解説します" in draft.segments[0].narration

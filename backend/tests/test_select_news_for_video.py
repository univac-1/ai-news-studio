from app.schemas.news import NewsItem
from app.services.select_news_for_video import dedupe_similar_news


def _item(
    item_id: str,
    title: str,
    summary: str,
    *,
    provider: str = "OpenAI",
    model_name: str = "GPT-5.6",
    impact: str = "",
    action: str = "",
) -> NewsItem:
    return NewsItem(
        id=item_id,
        title=title,
        summary=summary,
        impact=impact,
        action=action,
        provider=provider,
        model_name=model_name,
        importance="A",
    )


def test_dedupe_collapses_same_model_launch_from_different_angles():
    family = _item(
        "gpt-56-family",
        "GPT-5.6 new family",
        (
            'OpenAI announced the GPT-5.6 family "Luna", "Terra", and "Sol" '
            "with API pricing per 1M tokens."
        ),
        impact="Teams need to evaluate frontier model performance and cost.",
        action="Check benchmarks, pricing, and migration fit.",
    )
    efficiency = _item(
        "gpt-56-efficiency",
        "GPT-5.6 intelligence and efficiency improvements",
        (
            "OpenAI announced the new frontier model GPT-5.6, highlighting "
            "better intelligence per token, lower cost, and harder task support."
        ),
        impact="The improved model can upgrade existing AI applications.",
        action="Confirm GPT-5.6 pricing, performance, and API availability.",
    )

    assert dedupe_similar_news([family, efficiency]) == [family]


def test_dedupe_keeps_same_provider_different_model_versions():
    gpt_56 = _item("gpt-56", "GPT-5.6 launch", "OpenAI released GPT-5.6.")
    gpt_57 = _item(
        "gpt-57",
        "GPT-5.7 launch",
        "OpenAI released GPT-5.7.",
        model_name="GPT-5.7",
    )

    assert dedupe_similar_news([gpt_56, gpt_57]) == [gpt_56, gpt_57]

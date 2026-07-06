from ..schemas.draft import VideoPlanDraft
from .draft_store import save_draft
from .filter_priority import get_priority_a_news
from .generate_weekly_video_plan import generate_weekly_video_plan
from .polish_narration import polish_narration
from .refresh_news_with_search import refresh_news_with_search
from .select_news_for_video import dedupe_similar_news, select_news_for_video


class NoPriorityNewsError(Exception):
    pass


async def generate_new_weekly_draft() -> VideoPlanDraft:
    items = await get_priority_a_news(exclude_used=True)
    if not items:
        raise NoPriorityNewsError()

    items = await select_news_for_video(items)
    refresh_result = await refresh_news_with_search(items)
    refreshed_items = dedupe_similar_news(refresh_result.items)
    draft = generate_weekly_video_plan(refreshed_items)
    if refresh_result.reference_urls:
        reference_urls = list(
            dict.fromkeys([*draft.reference_urls, *refresh_result.reference_urls])
        )
        draft = draft.model_copy(update={"reference_urls": reference_urls})

    draft = await polish_narration(draft)
    save_draft(draft)
    return draft

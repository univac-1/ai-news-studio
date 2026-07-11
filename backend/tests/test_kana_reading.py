from app.services.kana_reading import to_voice_text


def test_ai_expert_is_read_with_japanese_term_reading():
    assert to_voice_text("次にAI専門家が解説します。") == "次にエーアイせんもんかが解説します。"


def test_ai_news_still_uses_ai_reading():
    assert to_voice_text("AIニュースをお届けします。") == "エーアイニュースをお届けします。"

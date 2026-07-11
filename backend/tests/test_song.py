import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.draft import VideoPlanDraft, VideoSegment
from app.services import song


class TestSplitMoras:
    def test_plain_hiragana(self):
        assert song.split_moras("あいうえお") == ["あ", "い", "う", "え", "お"]

    def test_youon_hiragana(self):
        # きゃ は1モーラ
        assert song.split_moras("きゃく") == ["きゃ", "く"]

    def test_youon_katakana(self):
        # シャ は1モーラ
        assert song.split_moras("シャワー") == ["シャ", "ワ", "ー"]

    def test_sokuon_is_its_own_mora(self):
        assert song.split_moras("がっこう") == ["が", "っ", "こ", "う"]

    def test_moraic_n_is_its_own_mora(self):
        assert song.split_moras("こんにちは") == ["こ", "ん", "に", "ち", "は"]

    def test_long_vowel_mark_is_its_own_mora(self):
        assert song.split_moras("コーヒー") == ["コ", "ー", "ヒ", "ー"]

    def test_small_vowel_combines_with_preceding(self):
        # ファ のような小書き母音も直前と結合して1モーラ
        assert song.split_moras("ふぁん") == ["ふぁ", "ん"]

    def test_mixed_hiragana_katakana(self):
        assert song.split_moras("こんしゅうのニュース") == [
            "こ", "ん", "しゅ", "う", "の", "ニュ", "ー", "ス",
        ]

    def test_rejects_kanji(self):
        with pytest.raises(ValueError):
            song.split_moras("今週")

    def test_rejects_ascii(self):
        with pytest.raises(ValueError):
            song.split_moras("AI")


class TestCountMoras:
    def test_matches_split_length(self):
        assert song.count_moras("こんしゅうもげんき") == 8


class TestFallbackLyrics:
    def test_phrase_mora_budgets_are_12_12_12_10(self):
        assert song.PHRASE_MORA_BUDGETS == (12, 12, 12, 10)

    def test_matches_phrase_budgets_exactly(self):
        assert len(song.FALLBACK_LYRICS) == len(song.PHRASE_MORA_BUDGETS)
        for phrase_text, budget in zip(song.FALLBACK_LYRICS, song.PHRASE_MORA_BUDGETS):
            assert song.count_moras(phrase_text) == budget


def _draft() -> VideoPlanDraft:
    return VideoPlanDraft(
        title="今週のAIニュース",
        week_label="2026年7月第1週",
        thumbnail_text="AI速報",
        intro="今週も始まるのだ",
        segments=[
            VideoSegment(
                number=1,
                headline="Example AI product launched",
                summary="summary",
                impact="impact",
                action="action",
                slide_title="slide",
                narration="narration",
                title_ja="サンプルAI製品が登場",
            ),
        ],
        outro="また来週なのだ",
        slide_outline=[],
        narration_script="",
        description="",
        hashtags=[],
        reference_urls=[],
        total_items=1,
        generated_at="2026-07-07T00:00:00Z",
    )


def _model_with_responses(payloads: list[str]) -> MagicMock:
    """generate_content_asyncがpayloadsを順に返すモックモデル。
    要素が尽きたら最後の要素を繰り返す(何度リトライしても同じ応答を返すテスト用)。"""
    model = MagicMock()
    state = {"i": 0}

    async def _generate(prompt):
        i = min(state["i"], len(payloads) - 1)
        state["i"] += 1
        return MagicMock(text=payloads[i])

    model.generate_content_async = AsyncMock(side_effect=_generate)
    return model


class TestGenerateSongLyrics:
    def _patched(self, model: MagicMock):
        return (
            patch.object(song.settings, "GEMINI_PROJECT", "test-project"),
            patch.object(song.vertexai, "init"),
            patch.object(song, "GenerativeModel", return_value=model),
        )

    @pytest.mark.asyncio
    async def test_all_slots_solved_on_first_attempt(self):
        payload = json.dumps(
            {
                "phrases": [
                    ["ずんずんエーアイニュースだ", "だみー1", "だみー2"],
                    ["サンプルエーアイきたのだ", "だみー1", "だみー2"],
                    ["モデルとツールもチェックだ", "だみー1", "だみー2"],
                    ["いっしょにチェックなのだ", "だみー1", "だみー2"],
                ]
            },
            ensure_ascii=False,
        )
        model = _model_with_responses([payload])
        p1, p2, p3 = self._patched(model)

        with p1, p2, p3:
            result = await song.generate_song_lyrics(_draft())

        assert result == [
            "ずんずんエーアイニュースだ",
            "サンプルエーアイきたのだ",
            "モデルとツールもチェックだ",
            "いっしょにチェックなのだ",
        ]
        assert model.generate_content_async.call_count == 1

    @pytest.mark.asyncio
    async def test_candidate_selection_skips_invalid_candidates(self):
        # 各スロットとも先頭候補は不正(モーラ数不足 or かな以外の文字)で、
        # 2番目の候補だけがモーラ数条件を満たす。2番目が採用されることを確認する。
        payload = json.dumps(
            {
                "phrases": [
                    ["ダメ", "ずんずんエーアイニュースだ", "だみー"],
                    ["AIニュース", "サンプルエーアイきたのだ", "だみー"],
                    ["みじかい", "モデルとツールもチェックだ", "だみー"],
                    ["ながすぎるふれーずだよ", "いっしょにチェックなのだ", "だみー"],
                ]
            },
            ensure_ascii=False,
        )
        model = _model_with_responses([payload])
        p1, p2, p3 = self._patched(model)

        with p1, p2, p3:
            result = await song.generate_song_lyrics(_draft())

        assert result == [
            "ずんずんエーアイニュースだ",
            "サンプルエーアイきたのだ",
            "モデルとツールもチェックだ",
            "いっしょにチェックなのだ",
        ]
        assert model.generate_content_async.call_count == 1

    @pytest.mark.asyncio
    async def test_middle_phrases_skip_generic_news_candidates(self):
        # 2・3フレーズ目はニュースの具体性が必要なため、モーラ数が合っていても
        # 抽象的すぎる候補はスキップし、次の具体寄り候補を採用する。
        payload = json.dumps(
            {
                "phrases": [
                    ["ずんずんエーアイニュースだ", "だみー1", "だみー2"],
                    ["わだいのニュースをおとどけ", "サンプルエーアイきたのだ", "だみー"],
                    ["すごいはなしがきたのだよ", "モデルとツールもチェックだ", "だみー"],
                    ["いっしょにチェックなのだ", "だみー1", "だみー2"],
                ]
            },
            ensure_ascii=False,
        )
        model = _model_with_responses([payload])
        p1, p2, p3 = self._patched(model)

        with p1, p2, p3:
            result = await song.generate_song_lyrics(_draft())

        assert result == [
            "ずんずんエーアイニュースだ",
            "サンプルエーアイきたのだ",
            "モデルとツールもチェックだ",
            "いっしょにチェックなのだ",
        ]
        assert model.generate_content_async.call_count == 1

    @pytest.mark.asyncio
    async def test_unsolved_slot_falls_back_others_kept(self):
        # スロット1(0-indexで1番目)だけは何度リトライしても不正な候補しか
        # 返ってこないケース。他の3スロットはGeminiの結果を維持しつつ、
        # スロット1だけFALLBACK_LYRICSで個別に補われることを確認する。
        payload = json.dumps(
            {
                "phrases": [
                    ["ずんずんエーアイニュースだ", "だみー1", "だみー2"],
                    ["だめ", "だめだめ", "ぜんぶだめ"],
                    ["モデルとツールもチェックだ", "だみー1", "だみー2"],
                    ["いっしょにチェックなのだ", "だみー1", "だみー2"],
                ]
            },
            ensure_ascii=False,
        )
        model = _model_with_responses([payload])
        p1, p2, p3 = self._patched(model)

        with p1, p2, p3:
            result = await song.generate_song_lyrics(_draft())

        assert result[0] == "ずんずんエーアイニュースだ"
        assert result[1] == song.FALLBACK_LYRICS[1]
        assert result[2] == "モデルとツールもチェックだ"
        assert result[3] == "いっしょにチェックなのだ"
        # 未解決スロットが残る限り、上限の5回までリトライする
        assert model.generate_content_async.call_count == 5

    @pytest.mark.asyncio
    async def test_plain_string_phrases_still_accepted(self):
        # 旧フォーマット(候補配列ではなく単一文字列)も引き続き受理する
        payload = json.dumps(
            {
                "phrases": [
                    "ずんずんエーアイニュースだ",
                    "サンプルエーアイきたのだ",
                    "モデルとツールもチェックだ",
                    "いっしょにチェックなのだ",
                ]
            },
            ensure_ascii=False,
        )
        model = _model_with_responses([payload])
        p1, p2, p3 = self._patched(model)

        with p1, p2, p3:
            result = await song.generate_song_lyrics(_draft())

        assert result == [
            "ずんずんエーアイニュースだ",
            "サンプルエーアイきたのだ",
            "モデルとツールもチェックだ",
            "いっしょにチェックなのだ",
        ]

    @pytest.mark.asyncio
    async def test_returns_fallback_when_gemini_project_unset(self):
        with patch.object(song.settings, "GEMINI_PROJECT", ""):
            result = await song.generate_song_lyrics(_draft())

        assert result == song.FALLBACK_LYRICS

    @pytest.mark.asyncio
    async def test_all_slots_unresolved_returns_full_fallback(self):
        payload = json.dumps(
            {
                "phrases": [
                    ["だめ", "だめだめ", "ぜんぶだめ"],
                    ["だめ", "だめだめ", "ぜんぶだめ"],
                    ["だめ", "だめだめ", "ぜんぶだめ"],
                    ["だめ", "だめだめ", "ぜんぶだめ"],
                ]
            },
            ensure_ascii=False,
        )
        model = _model_with_responses([payload])
        p1, p2, p3 = self._patched(model)

        with p1, p2, p3:
            result = await song.generate_song_lyrics(_draft())

        assert result == song.FALLBACK_LYRICS
        assert model.generate_content_async.call_count == 5

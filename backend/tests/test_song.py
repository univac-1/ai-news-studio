from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestAssignLyricsToNotes:
    def test_happy_path(self):
        phrase = song.MELODY_TEMPLATE[0]
        notes = song.assign_lyrics_to_notes("こんしゅうもげんき", phrase)
        assert len(notes) == len(phrase.notes)
        assert [n["lyric"] for n in notes] == song.split_moras("こんしゅうもげんき")
        assert notes[0]["key"] == phrase.notes[0].key
        assert notes[0]["frame_length"] == phrase.notes[0].frame_length

    def test_mora_count_mismatch_raises(self):
        phrase = song.MELODY_TEMPLATE[0]
        with pytest.raises(ValueError):
            song.assign_lyrics_to_notes("たりない", phrase)


class TestBuildScore:
    def test_leading_and_trailing_rests_present(self):
        score = song.build_score(song.FALLBACK_LYRICS)
        notes = score["notes"]
        assert notes[0]["key"] is None
        assert notes[0]["frame_length"] == song.LEADING_REST_FRAMES
        assert notes[0]["lyric"] == ""
        assert notes[-1]["key"] is None
        assert notes[-1]["frame_length"] == song.TRAILING_REST_FRAMES
        assert notes[-1]["lyric"] == ""

    def test_total_note_count(self):
        score = song.build_score(song.FALLBACK_LYRICS)
        notes = score["notes"]
        sung_notes = sum(len(phrase.notes) for phrase in song.MELODY_TEMPLATE)
        # 先頭休符 + 歌唱ノート + フレーズ間休符(フレーズ数-1) + 末尾休符
        expected = 1 + sung_notes + (len(song.MELODY_TEMPLATE) - 1) + 1
        assert len(notes) == expected

    def test_rest_notes_have_none_key_and_empty_lyric(self):
        score = song.build_score(song.FALLBACK_LYRICS)
        rest_notes = [n for n in score["notes"] if n["key"] is None]
        assert len(rest_notes) == len(song.MELODY_TEMPLATE) + 1  # 先頭+フレーズ間+末尾
        for note in rest_notes:
            assert note["lyric"] == ""

    def test_wrong_phrase_count_raises(self):
        with pytest.raises(ValueError):
            song.build_score(["ひとつだけ"])


class TestFallbackLyrics:
    def test_matches_melody_budgets_exactly(self):
        assert len(song.FALLBACK_LYRICS) == len(song.MELODY_TEMPLATE)
        for phrase_text, phrase in zip(song.FALLBACK_LYRICS, song.MELODY_TEMPLATE):
            assert song.count_moras(phrase_text) == len(phrase.notes)


class TestPhraseTimings:
    def test_durations_sum_matches_score_total(self):
        score = song.build_score(song.FALLBACK_LYRICS)
        total_frames = sum(note["frame_length"] for note in score["notes"])

        timings = song.phrase_timings(song.FALLBACK_LYRICS)
        total_seconds = sum(duration for _, duration in timings)

        assert total_seconds == pytest.approx(total_frames / song.FRAMES_PER_SECOND)

    def test_entry_count(self):
        timings = song.phrase_timings(song.FALLBACK_LYRICS)
        # 先頭無音 + フレーズ数
        assert len(timings) == 1 + len(song.MELODY_TEMPLATE)
        assert timings[0][0] == ""

    def test_wrong_phrase_count_raises(self):
        with pytest.raises(ValueError):
            song.phrase_timings(["ひとつだけ"])


def _mock_client_with(response=None, side_effect=None):
    client = AsyncMock()
    client.get = AsyncMock(return_value=response, side_effect=side_effect)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestCheckSongSupport:
    def setup_method(self):
        song._song_support_cache = None

    def teardown_method(self):
        song._song_support_cache = None

    @pytest.mark.asyncio
    async def test_true_result_is_cached_across_calls(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = [
            {
                "styles": [
                    {"type": "singing_teacher", "id": 6000},
                    {"type": "frame_decode", "id": 3061},
                ]
            }
        ]
        client = _mock_client_with(response=response)

        with patch("app.services.song.httpx.AsyncClient", return_value=client) as ctor:
            first = await song.check_song_support()
            second = await song.check_song_support()

        assert first is True
        assert second is True
        assert ctor.call_count == 1

    @pytest.mark.asyncio
    async def test_missing_style_is_not_cached_and_retries(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = [{"styles": [{"type": "talk", "id": 3}]}]
        client = _mock_client_with(response=response)

        with patch("app.services.song.httpx.AsyncClient", return_value=client) as ctor:
            first = await song.check_song_support()
            second = await song.check_song_support()

        assert first is False
        assert second is False
        assert ctor.call_count == 2

    @pytest.mark.asyncio
    async def test_exception_is_not_cached_and_retries(self):
        client = _mock_client_with(side_effect=RuntimeError("connection refused"))

        with patch("app.services.song.httpx.AsyncClient", return_value=client) as ctor:
            first = await song.check_song_support()
            second = await song.check_song_support()

        assert first is False
        assert second is False
        assert ctor.call_count == 2

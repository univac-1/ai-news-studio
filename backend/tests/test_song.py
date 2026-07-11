import json
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
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


class TestAssignLyricsToNotes:
    def test_happy_path(self):
        # 長音符「ー」を含まないテキストで、変換なしにそのまま割り当てられることを確認する
        phrase = song.MELODY_TEMPLATE[0]
        text = "こんしゅうもげんきいっぱい"
        notes = song.assign_lyrics_to_notes(text, phrase)
        assert len(notes) == len(phrase.notes)
        assert [n["lyric"] for n in notes] == song.split_moras(text)
        assert notes[0]["key"] == phrase.notes[0].key
        assert notes[0]["frame_length"] == phrase.notes[0].frame_length

    def test_mora_count_mismatch_raises(self):
        phrase = song.MELODY_TEMPLATE[0]
        with pytest.raises(ValueError):
            song.assign_lyrics_to_notes("たりない", phrase)

    def test_long_vowel_mark_is_replaced_with_preceding_vowel(self):
        # VOICEVOXの歌唱合成は「ー」を歌詞として受け付けないため、
        # 直前モーラの母音に置き換える(えー→ええ)
        phrase = song.MELODY_TEMPLATE[0]
        notes = song.assign_lyrics_to_notes("えーあいのはなしをきくよ", phrase)
        assert [n["lyric"] for n in notes] == [
            "え", "え", "あ", "い", "の", "は", "な", "し", "を", "き", "く", "よ",
        ]

    def test_long_vowel_mark_after_youon_uses_youon_vowel(self):
        # ニュー → ニュ + う (拗音ゅの母音はu)
        phrase = song.MELODY_TEMPLATE[0]
        notes = song.assign_lyrics_to_notes("ニューすをきいてほしいのだ", phrase)
        assert [n["lyric"] for n in notes] == [
            "ニュ", "う", "す", "を", "き", "い", "て", "ほ", "し", "い", "の", "だ",
        ]

    def test_consecutive_long_vowel_marks(self):
        # コーー → コ + お + お (2つ目のーは置き換え後の母音を引き継ぐ)
        phrase = song.MELODY_TEMPLATE[0]
        notes = song.assign_lyrics_to_notes("コーーひをたのしくのむよ", phrase)
        assert [n["lyric"] for n in notes] == [
            "コ", "お", "お", "ひ", "を", "た", "の", "し", "く", "の", "む", "よ",
        ]

    def test_fallback_lyrics_produce_no_long_vowel_lyric(self):
        score = song.build_score(song.FALLBACK_LYRICS)
        assert all(note["lyric"] != "ー" for note in score["notes"])


class TestNoteConstantsGridAligned:
    """伴奏(song_backing)のビートグリッドと一致させるため、すべての音価・休符が
    EIGHTH_FRAMESの整数倍であることを保証する回帰テスト。"""

    def test_named_constants_are_multiples_of_eighth(self):
        constants = [
            song.EIGHTH_FRAMES,
            song.QUARTER_FRAMES,
            song.DOTTED_QUARTER_FRAMES,
            song.HALF_FRAMES,
            song.DOTTED_HALF_FRAMES,
            song.WHOLE_FRAMES,
            song.LEADING_REST_FRAMES,
            song.TRAILING_REST_FRAMES,
            song.INTER_PHRASE_REST_FRAMES,
        ]
        for value in constants:
            assert value % song.EIGHTH_FRAMES == 0

    def test_every_melody_note_is_a_multiple_of_eighth(self):
        for phrase in song.MELODY_TEMPLATE:
            for note in phrase.notes:
                assert note.frame_length % song.EIGHTH_FRAMES == 0

    def test_every_melody_note_frame_length_is_a_known_constant(self):
        known = {
            song.EIGHTH_FRAMES,
            song.QUARTER_FRAMES,
            song.DOTTED_QUARTER_FRAMES,
            song.HALF_FRAMES,
            song.DOTTED_HALF_FRAMES,
            song.WHOLE_FRAMES,
        }
        for phrase in song.MELODY_TEMPLATE:
            for note in phrase.notes:
                assert note.frame_length in known

    def test_phrase_mora_budgets_are_12_12_12_10(self):
        assert song.PHRASE_MORA_BUDGETS == (12, 12, 12, 10)


def _write_mono16_wav(
    path, seconds: float = 2.0, sample_rate: int = 24000, freq: float = 440.0, amplitude: float = 0.05
) -> np.ndarray:
    """テスト用の16bitモノラルwav(静かな440Hzサイン波)を書き出し、サンプル列を返す。"""
    n = int(seconds * sample_rate)
    t = np.arange(n) / sample_rate
    samples = (amplitude * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return samples


class TestMixBackingIntoWav:
    def test_mixes_backing_and_preserves_format(self, tmp_path):
        path = tmp_path / "song.wav"
        original = _write_mono16_wav(path)

        song._mix_backing_into_wav(path)

        with wave.open(str(path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 24000
            n_frames = wf.getnframes()
            mixed = np.frombuffer(wf.readframes(n_frames), dtype=np.int16)

        assert n_frames == len(original)
        assert not np.array_equal(mixed, original)

    def test_handles_arbitrary_length_not_tied_to_real_score(self, tmp_path):
        # 実際のスコア長(1600フレーム相当)に一致しない任意の長さでも動作すること
        path = tmp_path / "short.wav"
        original = _write_mono16_wav(path, seconds=0.37)

        song._mix_backing_into_wav(path)

        with wave.open(str(path), "rb") as wf:
            assert wf.getsampwidth() == 2
            assert wf.getnchannels() == 1
            assert wf.getnframes() == len(original)

    def test_skips_mixing_when_disabled(self, tmp_path):
        path = tmp_path / "song.wav"
        _write_mono16_wav(path)
        original_bytes = path.read_bytes()

        with patch.object(song.settings, "SONG_BACKING_ENABLED", False):
            song._mix_backing_into_wav(path)

        assert path.read_bytes() == original_bytes

    def test_skips_mixing_when_not_mono16(self, tmp_path, caplog):
        path = tmp_path / "stereo.wav"
        n = 100
        samples = np.zeros(n * 2, dtype=np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(samples.tobytes())
        original_bytes = path.read_bytes()

        song._mix_backing_into_wav(path)

        assert path.read_bytes() == original_bytes


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

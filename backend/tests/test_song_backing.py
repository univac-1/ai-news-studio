import numpy as np
import pytest

from app.services import song_backing


# だいたい実際のソング(1600フレーム, 93.75fps, 24000Hz)に近い設定を既定値として使う
FRAMES_PER_SECOND = 93.75
EIGHTH_FRAMES = 20
SAMPLE_RATE = 24000
PHRASE_FRAME_SPANS = [(40, 400), (420, 780), (800, 1200), (1220, 1520)]
TOTAL_FRAMES = 1600


def _total_samples(total_frames: int = TOTAL_FRAMES, sample_rate: int = SAMPLE_RATE) -> int:
    return round(total_frames / FRAMES_PER_SECOND * sample_rate)


class TestRenderBackingTrack:
    def test_returns_exact_requested_length(self):
        total_samples = _total_samples()
        result = song_backing.render_backing_track(
            total_samples=total_samples,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=PHRASE_FRAME_SPANS,
        )
        assert len(result) == total_samples
        assert result.dtype == np.float32

    def test_peak_does_not_exceed_ceiling(self):
        total_samples = _total_samples()
        result = song_backing.render_backing_track(
            total_samples=total_samples,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=PHRASE_FRAME_SPANS,
        )
        assert np.max(np.abs(result)) <= 0.95

    def test_deterministic_across_calls(self):
        total_samples = _total_samples()
        first = song_backing.render_backing_track(
            total_samples=total_samples,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=PHRASE_FRAME_SPANS,
        )
        second = song_backing.render_backing_track(
            total_samples=total_samples,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=PHRASE_FRAME_SPANS,
        )
        assert np.array_equal(first, second)

    def test_non_silent(self):
        total_samples = _total_samples()
        result = song_backing.render_backing_track(
            total_samples=total_samples,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=PHRASE_FRAME_SPANS,
        )
        rms = float(np.sqrt(np.mean(np.square(result))))
        assert rms > 0.01

    def test_first_beat_has_kick_energy(self):
        total_samples = _total_samples()
        result = song_backing.render_backing_track(
            total_samples=total_samples,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=PHRASE_FRAME_SPANS,
        )
        first_100ms = result[: round(0.1 * SAMPLE_RATE)]
        rms = float(np.sqrt(np.mean(np.square(first_100ms))))
        assert rms > 0.01

    def test_handles_empty_phrase_spans_without_crashing(self):
        # フレーズ区間が空でも(異常系のテスト呼び出し等)クラッシュしない
        total_samples = round(1.0 * SAMPLE_RATE)
        result = song_backing.render_backing_track(
            total_samples=total_samples,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=[],
        )
        assert len(result) == total_samples
        assert np.max(np.abs(result)) <= 0.95

    def test_handles_zero_total_samples(self):
        result = song_backing.render_backing_track(
            total_samples=0,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=PHRASE_FRAME_SPANS,
        )
        assert len(result) == 0

    def test_trailing_region_is_quiet_after_the_final_beat(self):
        # 末尾休符では最初の拍だけキック+ベースを鳴らし、そのあとは何も鳴らさない。
        # 末尾休符の開始から十分後(サステインベースの減衰後)は静かになっているはず。
        # 実スコア長(末尾休符は0.85秒しかない)より長めのバッファを与えて減衰の余地を作る。
        trailing_start_frame = PHRASE_FRAME_SPANS[-1][1]
        trailing_start_sample = round(trailing_start_frame / FRAMES_PER_SECOND * SAMPLE_RATE)
        total_samples = trailing_start_sample + round(2.0 * SAMPLE_RATE)

        result = song_backing.render_backing_track(
            total_samples=total_samples,
            sample_rate=SAMPLE_RATE,
            frames_per_second=FRAMES_PER_SECOND,
            eighth_frames=EIGHTH_FRAMES,
            phrase_frame_spans=PHRASE_FRAME_SPANS,
        )
        # サステインベース(約1.2秒で減衰)が終わったあとの領域を見る
        tail_start = trailing_start_sample + round(1.5 * SAMPLE_RATE)
        tail = result[tail_start:]
        rms = float(np.sqrt(np.mean(np.square(tail))))
        assert rms < 0.01

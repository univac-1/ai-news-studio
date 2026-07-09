"""ずんだもんニュースソング用の伴奏(ドラム+ベース)を純粋に合成するモジュール。

- app.services.song には依存しない(song.py側からこちらをimportする一方通行にして
  循環importを避けるため)。
- すべての乱数はシード固定のnumpy Generatorを使うため、同じ引数なら常に同じ波形を返す。
- ビートグリッドは8分音符(eighth_frames)単位。四分拍 = 8分音符2つ、
  1小節 = 四分拍4つ(= 8分音符8つ)としてt=0から数える。
"""

from __future__ import annotations

import numpy as np

# 乱数シード(固定)。再現性のため常にこの値でnumpy.random.Generatorを作り直す。
_RNG_SEED = 20260707

# ベースのルート音(Hz)
_C2_HZ = 65.41
_A1_HZ = 55.00
_G1_HZ = 49.00

# ミックスレベル(概算)
_KICK_GAIN = 0.35
_CLAP_GAIN = 0.22
_HAT_GAIN = 0.10
_BASS_GAIN = 0.26

# ハイハットのオフビート(裏拍)強調係数
_HAT_OFFBEAT_BOOST = 1.25

# ソフトクリップ後に許容するピーク値
_PEAK_CEILING = 0.95

# 各ワンショット系(kick/clap/hat)のエンベロープは exp(-5*t/duration) で統一する。
# duration経過時点でおよそ0.7%まで減衰し、自然にゼロへ収束してクリック雑音を避けられる。
_ENVELOPE_K = 5.0


def _envelope(t: np.ndarray, duration: float) -> np.ndarray:
    if duration <= 0:
        return np.ones_like(t)
    return np.exp(-_ENVELOPE_K * t / duration)


def _make_kick(sample_rate: int) -> np.ndarray:
    """ピッチが150Hz→50Hzへ90msで下降するキック音(全長0.6秒、指数減衰)。"""
    duration = 0.6
    sweep_duration = 0.09
    start_freq, end_freq = 150.0, 50.0

    n = max(1, int(duration * sample_rate))
    t = np.arange(n) / sample_rate
    freq = np.where(
        t < sweep_duration,
        start_freq + (end_freq - start_freq) * (t / sweep_duration),
        end_freq,
    )
    # 瞬時周波数を積分して位相にすることで、周波数が滑らかに変化しても位相不連続にならない
    phase = 2 * np.pi * np.cumsum(freq) / sample_rate
    wave = np.sin(phase) * _envelope(t, duration)
    return wave.astype(np.float32)


def _make_noise_burst(sample_rate: int, rng: np.random.Generator, duration: float) -> np.ndarray:
    """ホワイトノイズに1次差分フィルタ(簡易ハイパス)をかけたバースト音。

    クラップ・ハイハットの両方で使う。差分フィルタで低域(こもり・ランブル)を削る。
    """
    n = max(1, int(duration * sample_rate))
    noise = rng.standard_normal(n)
    filtered = np.diff(noise, prepend=noise[:1])
    t = np.arange(n) / sample_rate
    wave = filtered * _envelope(t, duration)
    peak = float(np.max(np.abs(wave)))
    if peak > 0:
        wave = wave / peak
    return wave.astype(np.float32)


def _make_clap(sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    return _make_noise_burst(sample_rate, rng, duration=0.08)


def _make_hat(sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    return _make_noise_burst(sample_rate, rng, duration=0.025)


def _make_bass_note(
    freq_hz: float, sample_rate: int, n_samples: int, decay_duration: float
) -> np.ndarray:
    """サイン波+軽い2倍音のプラック風ベース音。"""
    if n_samples <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.arange(n_samples) / sample_rate
    wave = np.sin(2 * np.pi * freq_hz * t) + 0.15 * np.sin(2 * np.pi * 2 * freq_hz * t)
    wave = wave / 1.15
    wave = wave * _envelope(t, decay_duration)
    return wave.astype(np.float32)


def _add_at(buffer: np.ndarray, start: int, event: np.ndarray, gain: float) -> None:
    """eventをbuffer[start:]へ加算する。バッファ範囲外にはみ出す分は切り詰める。"""
    if start >= len(buffer) or len(event) == 0:
        return
    end = start + len(event)
    if end > len(buffer):
        event = event[: len(buffer) - start]
        end = len(buffer)
    if len(event) == 0:
        return
    buffer[start:end] += event * gain


def _bass_root_hz(frame_pos: int, phrase_frame_spans: list[tuple[int, int]]) -> float:
    """指定フレーム位置でのベースのルート音を決める。

    フレーズ1・2・4はC2、フレーズ3(0-indexで2番目)は前半A1・後半G1。
    先頭の無音・フレーズ間の無音・末尾無音(参考として)はC2で伴奏を続ける。
    """
    if not phrase_frame_spans:
        return _C2_HZ
    if frame_pos < phrase_frame_spans[0][0]:
        return _C2_HZ  # 歌い出し前の無音
    if frame_pos >= phrase_frame_spans[-1][1]:
        return _C2_HZ  # 歌い終わり後の無音(通常は別処理で使われない)
    for i, (start, end) in enumerate(phrase_frame_spans):
        if start <= frame_pos < end:
            if i == 2 and len(phrase_frame_spans) > 2:
                midpoint = (start + end) / 2
                return _A1_HZ if frame_pos < midpoint else _G1_HZ
            return _C2_HZ
    return _C2_HZ  # フレーズ間の無音


def render_backing_track(
    total_samples: int,
    sample_rate: int,
    frames_per_second: float,
    eighth_frames: int,
    phrase_frame_spans: list[tuple[int, int]],
) -> np.ndarray:
    """ドラム(キック・クラップ・ハイハット)+ベースの伴奏トラックを合成する。

    8分音符グリッド(8分音符k = サンプル round(k * eighth_frames / frames_per_second
    * sample_rate))に沿ってイベントを配置する。t=0からの通し番号で4分拍・小節を数え、
    - キック: 各小節(4分拍4つ)の1拍目・3拍目
    - クラップ: 各小節の2拍目・4拍目
    - ハイハット: 毎8分音符(裏拍はやや強め)
    - ベース: 毎8分音符でルート音のプラック音(フレーズごとの和声に追従)
    を鳴らす。歌い終わり後の無音(末尾)に入った最初の拍でキック+サステインベースを
    1回だけ鳴らし、以降は完全に無音にしてきれいに終わる。

    戻り値は total_samples ちょうどの長さのfloat32モノラル配列([-1, 1]の範囲)。
    乱数はシード固定のGeneratorを使うため、同じ引数なら常に同じ波形を返す。
    """
    rng = np.random.default_rng(_RNG_SEED)
    buffer = np.zeros(max(0, total_samples), dtype=np.float32)

    trailing_start_frame = phrase_frame_spans[-1][1] if phrase_frame_spans else float("inf")

    eighth_seconds = eighth_frames / frames_per_second
    eighth_samples_len = max(1, round(eighth_seconds * sample_rate))

    trailing_placed = False
    k = 0
    while True:
        frame_pos = k * eighth_frames
        start_sample = round(frame_pos / frames_per_second * sample_rate)
        if start_sample >= total_samples:
            break

        if frame_pos >= trailing_start_frame:
            if not trailing_placed:
                _add_at(buffer, start_sample, _make_kick(sample_rate), _KICK_GAIN)
                sustain_duration = 1.2
                bass = _make_bass_note(
                    _C2_HZ,
                    sample_rate,
                    round(sustain_duration * sample_rate),
                    decay_duration=sustain_duration,
                )
                _add_at(buffer, start_sample, bass, _BASS_GAIN)
                trailing_placed = True
            k += 1
            continue

        eighth_in_bar = k % 8

        if eighth_in_bar in (0, 4):  # 4分拍1拍目・3拍目
            _add_at(buffer, start_sample, _make_kick(sample_rate), _KICK_GAIN)
        if eighth_in_bar in (2, 6):  # 4分拍2拍目・4拍目
            _add_at(buffer, start_sample, _make_clap(sample_rate, rng), _CLAP_GAIN)

        hat_gain = _HAT_GAIN * (_HAT_OFFBEAT_BOOST if k % 2 == 1 else 1.0)
        _add_at(buffer, start_sample, _make_hat(sample_rate, rng), hat_gain)

        root_hz = _bass_root_hz(frame_pos, phrase_frame_spans)
        note_len = round(0.9 * eighth_samples_len)
        bass = _make_bass_note(
            root_hz, sample_rate, note_len, decay_duration=note_len / sample_rate
        )
        _add_at(buffer, start_sample, bass, _BASS_GAIN)

        k += 1

    mixed = np.tanh(buffer)
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > _PEAK_CEILING:
        mixed = mixed * (_PEAK_CEILING / peak)
    return mixed.astype(np.float32)

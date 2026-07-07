"""VOICEVOX/Gemini なしのローカルE2Eスモークテスト。

`generate_video_from_draft` の全体パイプライン(ずんだもんニュースソングのコーナー込み)を、
ネットワークに依存する箇所だけ最小限モンキーパッチして実行する。

前提: ffmpeg/ffprobe がPATH上にあること。

使い方:
    cd backend
    uv run python scripts/smoke_video.py [--out DIR]
    uv run python scripts/smoke_video.py --review-mock  # 自己レビュー&自動リテイクの検証
"""

import argparse
import asyncio
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from PIL import Image  # noqa: E402

from app.core.config import settings as app_settings  # noqa: E402
from app.schemas.draft import VideoPlanDraft, VideoSegment  # noqa: E402
from app.schemas.video import ReviewFinding  # noqa: E402
from app.services import song as song_module  # noqa: E402
from app.services import video_generator  # noqa: E402
from app.services import video_review  # noqa: E402
from app.services.image_assets import ThemeImages  # noqa: E402


def build_fixture_draft() -> VideoPlanDraft:
    """2〜3セグメントの最小限だが妥当なドラフト(prepare_draft_for_videoの保証層を
    経由済みという想定で、intro_line/reaction_line/title_ja等をすべて埋めておく)。"""
    segments = [
        VideoSegment(
            number=1,
            headline="OpenAI released a new flagship model",
            summary="OpenAIが新しい旗艦AIモデルを発表した。",
            impact="生成AIの応答精度が大きく向上する。",
            action="公式ブログで詳細を確認しよう。",
            slide_title="#1 新モデル発表",
            narration=(
                "オープンエーアイが新しい旗艦モデルを発表しました。"
                "これまでよりも高精度な応答が可能になったとされています。"
            ),
            intro_line="まず最初のニュースなのだ。",
            reaction_line="すごい進化なのだ！",
            source="OpenAI Blog",
            title_ja="新モデル発表",
            category="general",
            rank_reason="性能向上のインパクトが大きいから",
        ),
        VideoSegment(
            number=2,
            headline="A security flaw was found in an AI browser extension",
            summary="AIブラウザ拡張機能に脆弱性が見つかった。",
            impact="個人情報が漏えいするリスクがある。",
            action="拡張機能をすぐに最新版へ更新しよう。",
            slide_title="#2 セキュリティ注意",
            narration=(
                "AIブラウザ拡張機能にセキュリティ上の脆弱性が発見されました。"
                "攻撃者が悪用すると情報が漏えいする可能性があります。"
            ),
            intro_line="次はセキュリティのニュースなのだ。",
            reaction_line="早めの対策が大事なのだ。",
            source="Security Week",
            title_ja="セキュリティ注意",
            category="security",
            rank_reason="情報漏えいリスクがあるから",
        ),
        VideoSegment(
            number=3,
            headline="A new developer tool speeds up coding workflows",
            summary="新しい開発者ツールでコーディングが高速化する。",
            impact="開発者の生産性が向上する。",
            action="無料トライアルを試してみよう。",
            slide_title="#3 開発ツール登場",
            narration=(
                "コーディングを高速化する新しい開発者ツールが公開されました。"
                "既存のエディタと連携して使えます。"
            ),
            intro_line="最後は開発ツールのニュースなのだ。",
            reaction_line="便利になったのだ。",
            source="Dev Weekly",
            title_ja="開発ツール登場",
            category="devtools",
            rank_reason="生産性向上に直結するから",
        ),
    ]

    return VideoPlanDraft(
        title="今週のAIニュースまとめ",
        title_candidates=["今週のAIニュースまとめ"],
        week_label="7/1〜7/6",
        thumbnail_text="今週のAI速報",
        thumbnail_text_candidates=["今週のAI速報"],
        hook="今週も気になるAIニュースが盛りだくさんなのだ！",
        intro="今週は3本のニュースを紹介するのだ。",
        segments=segments,
        outro="今週の重要度ランキングを見ていくのだ。",
        slide_outline=["hook", "opening", "segment1", "segment2", "segment3", "ranking"],
        narration_script="",
        description="今週のAIニュースまとめです。",
        hashtags=["#AI", "#ニュース"],
        reference_urls=[],
        total_items=3,
        generated_at="2026-07-06T00:00:00+00:00",
    )


def _write_sine_wav(path: Path, duration: float, freq: float | None, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = max(int(duration * sample_rate), 1)
    buf = bytearray()
    for i in range(n_samples):
        if freq is None:
            sample = 0
        else:
            sample = int(8000 * math.sin(2 * math.pi * freq * i / sample_rate))
        buf += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(buf))


async def fake_synthesize_voice(
    text: str,
    path: Path,
    reading_map: dict[str, str] | None = None,
    speaker_id: int = 0,
) -> list[tuple[str, float]]:
    """_synthesize_voiceの代替。VOICEVOXを呼ばず、文ごとにサイン波チャンクを
    書き出して結合する。チャンク分割は実装と同じ_split_voice_textを再利用する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks = video_generator._split_voice_text(text)
    chunk_dir = path.parent / f"{path.stem}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_paths: list[Path] = []
    chunk_durations: list[tuple[str, float]] = []
    for index, chunk in enumerate(chunks, 1):
        duration = max(len(chunk) / 9.0, 0.3)
        chunk_path = chunk_dir / f"{path.stem}_{index:03}.wav"
        _write_sine_wav(chunk_path, duration, freq=440.0)
        chunk_paths.append(chunk_path)
        chunk_durations.append((chunk, duration))
    video_generator._concat_wavs(chunk_paths, path)
    return chunk_durations


async def fake_check_song_support() -> bool:
    return True


async def fake_generate_song_lyrics(draft: VideoPlanDraft) -> list[str]:
    return list(song_module.FALLBACK_LYRICS)


async def fake_synthesize_song(phrases: list[str], out_path: Path) -> list[tuple[str, float]]:
    """synthesize_songの代替。VOICEVOXを呼ばず、MELODY_TEMPLATEのMIDIノート番号を
    サイン波に変換して書き出す(休符は無音)。戻り値は実装と同じphrase_timings。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame_rate = song_module.FRAMES_PER_SECOND
    sample_rate = 24000

    notes: list[tuple[int | None, int]] = [(None, song_module.LEADING_REST_FRAMES)]
    last_index = len(song_module.MELODY_TEMPLATE) - 1
    for i, phrase in enumerate(song_module.MELODY_TEMPLATE):
        for note in phrase.notes:
            notes.append((note.key, note.frame_length))
        notes.append(
            (None, song_module.INTER_PHRASE_REST_FRAMES if i < last_index else song_module.TRAILING_REST_FRAMES)
        )

    buf = bytearray()
    for key, frame_length in notes:
        duration = frame_length / frame_rate
        n_samples = max(int(duration * sample_rate), 1)
        if key is None:
            buf += b"\x00\x00" * n_samples
        else:
            freq = 440.0 * 2 ** ((key - 69) / 12)
            for i in range(n_samples):
                sample = int(8000 * math.sin(2 * math.pi * freq * i / sample_rate))
                buf += struct.pack("<h", sample)

    with wave.open(str(out_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(buf))

    return song_module.phrase_timings(phrases)


async def fake_generate_theme_images(draft: VideoPlanDraft) -> ThemeImages:
    # サムネイル生成はローカル合成前提でthumbnail_bgの非Noneを要求するため、
    # 単色画像で代替する(Gemini画像生成は呼ばない)。
    bg = Image.new("RGB", (1280, 720), (18, 22, 46))
    return ThemeImages(thumbnail_bg=bg)


async def fake_generate_segment_images(segments: list[VideoSegment]) -> dict[int, Image.Image]:
    return {}


async def fake_build_reading_map(texts: list[str]) -> dict[str, str]:
    return {}


def apply_patches() -> None:
    video_generator._synthesize_voice = fake_synthesize_voice
    video_generator.check_song_support = fake_check_song_support
    video_generator.generate_song_lyrics = fake_generate_song_lyrics
    video_generator.synthesize_song = fake_synthesize_song
    video_generator.generate_theme_images = fake_generate_theme_images
    video_generator.generate_segment_images = fake_generate_segment_images
    video_generator.build_reading_map = fake_build_reading_map


class _ReviewMockState:
    """--review-mock用の状態。round=1で1件のtext_overflow指摘、round=2([]応答)で
    解消済みを模して、Gemini無しでreview_and_retakeの一巡(抽出→レビュー→リテイク→
    再アセンブル→再レビュー)を検証できるようにする。"""

    def __init__(self) -> None:
        self.round = 0
        self.flagged_part: int | None = None


def make_fake_review_parts(state: _ReviewMockState):
    async def fake_review_parts(frame_paths, part_contexts):
        state.round += 1
        if state.round == 1:
            segment_ctx = next(
                (ctx for ctx in part_contexts if ctx.get("kind") == "segment"), None
            )
            if segment_ctx is None:
                return []
            state.flagged_part = segment_ctx["part"]
            return [
                ReviewFinding(
                    part=state.flagged_part,
                    code="text_overflow",
                    description="(review-mock) 本文がスライド右端をはみ出している想定の疑似指摘",
                    fixable=True,
                )
            ]
        # round 2以降: リテイク済みパートは解消したものとして空配列を返す
        return []

    return fake_review_parts


def apply_review_mock_patches() -> _ReviewMockState:
    """--review-mock用。GEMINI_PROJECTを疑似値にしてreview_and_retakeを有効化しつつ、
    実際のGemini呼び出し(video_review.review_parts)だけをスタブに差し替える。
    review_and_retakeはvideo_review内でreview_partsをグローバル参照で呼ぶため、
    ここでの差し替えはvideo_generator側の`from .video_review import review_and_retake`
    経由の呼び出しにもそのまま反映される。"""
    app_settings.GEMINI_PROJECT = "smoke-test-project"
    app_settings.REVIEW_ENABLED = True
    state = _ReviewMockState()
    video_review.review_parts = make_fake_review_parts(state)
    return state


def _ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return float(result.stdout.strip())


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=str, default=None, help="出力先ディレクトリのルートを上書き(既定はdata/generated)"
    )
    parser.add_argument(
        "--review-mock",
        action="store_true",
        help="Gemini無しでvideo_review(自己レビュー&自動リテイク)の一巡を検証する",
    )
    args = parser.parse_args()
    if args.out:
        video_generator.GENERATED_DIR = Path(args.out)
        video_generator.GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    apply_patches()
    review_state = apply_review_mock_patches() if args.review_mock else None

    draft = build_fixture_draft()
    artifact = await video_generator.generate_video_from_draft(draft)

    work_dir = video_generator.GENERATED_DIR / artifact.id
    video_path = work_dir / artifact.video_path
    srt_path = work_dir / artifact.subtitles_path
    song_lyrics_path = work_dir / "song_lyrics.json"
    parts_dir = work_dir / "parts"

    assert video_path.exists(), f"video.mp4 が見つかりません: {video_path}"
    duration = _ffprobe_duration(video_path)
    assert duration > 30.0, f"動画尺が短すぎます: {duration:.2f}s"

    srt_text = srt_path.read_text(encoding="utf-8")
    for phrase in song_module.FALLBACK_LYRICS:
        assert phrase in srt_text, f"SRTにフォールバック歌詞が含まれていません: {phrase!r}"

    assert song_lyrics_path.exists(), "song_lyrics.json が生成されていません(歌パートがスキップされた?)"
    assert "ずんだもんニュースソング" in artifact.chapters, "チャプターに歌パートがありません"

    part_files = sorted(parts_dir.glob("part_*.mp4"))
    assert len(part_files) == artifact.slide_count, (
        f"partファイル数({len(part_files)})とslide_count({artifact.slide_count})が一致しません"
        "(歌パートスキップ時の連番ズレの可能性)"
    )

    if args.review_mock:
        assert review_state is not None and review_state.flagged_part is not None, (
            "review-mockがsegmentパートを検出できませんでした(フィクスチャ構成を確認してください)"
        )
        review_report_path = work_dir / "review_report.json"
        assert review_report_path.exists(), "review_report.json が生成されていません"
        report = json.loads(review_report_path.read_text(encoding="utf-8"))
        assert report["status"] in ("passed", "passed_with_warnings"), (
            f"review_report.jsonのstatusが不正です: {report['status']!r}"
        )
        assert report["rounds"] >= 1, f"レビューラウンド数が不正です: {report['rounds']}"
        # round1のtext_overflow指摘はround2の再レビューで解消(空配列)されている前提なので、
        # 最終findingsには残っていないはず(round-1-fix semantics)
        assert not any(
            f["part"] == review_state.flagged_part for f in report["findings"]
        ), "リテイクしたパートの指摘がfindingsに残っています(リテイクが反映されていない?)"
        assert report["status"] == "passed", (
            f"round1のtext_overflowはround2で解消される想定なのでstatus=passedになるはずです: {report}"
        )
        print(f"review-mock  : status={report['status']} rounds={report['rounds']}")

    print("SMOKE TEST OK")
    print(f"artifact dir : {work_dir}")
    print(f"video        : {video_path} ({duration:.2f}s)")
    print(f"slide_count  : {artifact.slide_count} (parts: {len(part_files)})")
    print("chapters:")
    print(artifact.chapters)


if __name__ == "__main__":
    asyncio.run(main())

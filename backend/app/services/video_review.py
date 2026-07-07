"""動画生成後の自己レビュー&自動リテイク(Feature C)。

完成した各パート(スライド1枚分の動画)からフレームを抜き出し、Geminiの
マルチモーダル1リクエストでQAチェックリストに沿ってレビューさせる。
修正可能(fixable)と判定された不具合は、呼び出し側(video_generator)から
渡されたコールバックでリテイク(再レンダリング+再アセンブル)し、最大2ラウンドまで
繰り返す。Gemini呼び出しが失敗しても動画生成そのものは絶対に落とさず、
その場合は status="skipped" として扱う。
"""

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Awaitable, Callable

from google import genai
from google.genai import types

from ..core.config import settings
from ..schemas.video import ReviewFinding, ReviewReport

logger = logging.getLogger(__name__)

REVIEW_MODEL = "gemini-2.5-flash"

# Geminiの判定に関わらず、コード側で機械的に決める修正可否のホワイトリスト。
# text_overflow/overlapはレイアウト起因でどのスライド種別でも compact=True の
# 再レンダリングで改善が見込めるため常にfixable扱い。
# contrast/empty_slideは「生成イラストが暗すぎる/ほぼ空」等、illustrationスライド
# 特有の画像差し替え(drop_image)でのみ改善が見込めるため、illustrationに限定する。
_ALWAYS_FIXABLE_CODES = {"text_overflow", "overlap"}
_ILLUSTRATION_ONLY_FIXABLE_CODES = {"contrast", "empty_slide"}

_QA_INSTRUCTIONS = (
    "あなたはAIニュース動画のQA(品質チェック)担当です。\n"
    "これから、動画の各パート(スライド1枚分)の代表フレーム画像を、そのパートの"
    "想定内容(テキスト情報)とセットで順番に渡します。画像を1枚ずつ確認し、"
    "次のQAチェックリストに明確に該当する不具合だけを報告してください。\n\n"
    "QAチェックリスト:\n"
    "- text_overflow: 文字が画面の外にはみ出している、または画面端で切れている\n"
    "- overlap: タイトル・字幕・キャラクターなど複数の要素が重なって読みにくい\n"
    "- contrast: 背景と文字の色が近く、文字が読み取れない\n"
    "- subtitle_mismatch: 画面下部の字幕テキストが、想定される内容と明らかに違う\n"
    "- empty_slide: スライドがほぼ空白で、伝えるべき内容がほとんど見えない\n\n"
    "重要な注意点:\n"
    "- 誤検知(false positive)はコストが高いです。明確に問題があると確信できる場合"
    "のみ報告してください。\n"
    "- 迷う場合、軽微なデザイン上の好みの違い、意図されたレイアウト(巨大な番号の"
    "透かしなど)は報告しないでください。\n"
    "- 問題が無いパートは報告に含めなくてよいです。\n\n"
    "出力は厳密なJSON配列のみ。前置きや説明、コードフェンスは付けないでください。"
    "問題が1件も無い場合は空配列 [] を返してください。\n"
    "各要素の形式: "
    '{"part": <パート番号(整数)>, "code": "<上記のいずれか>", '
    '"description": "<日本語での具体的な指摘内容>", "severity": "low|medium|high"}\n'
)


def _run_ffmpeg(args: list[str], cwd: Path | None = None) -> str:
    """video_generatorとの循環importを避けるためのローカルffmpegランナー。"""
    result = subprocess.run(
        ["ffmpeg", "-y", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] or "ffmpeg failed")
    return result.stderr


def extract_part_frames(
    work_dir: Path,
    part_durations: list[float],
    part_numbers: list[int] | None = None,
) -> list[Path]:
    """各パートの代表フレームをPNGで書き出す。

    part_numbersを省略した場合は1〜len(part_durations)の連番(全パート)を対象にする。
    リテイク後の再レビューでは対象パートだけのpart_numbersを渡して部分的に
    再抽出できる。
    """
    numbers = part_numbers if part_numbers is not None else list(range(1, len(part_durations) + 1))
    review_dir = work_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    frame_paths: list[Path] = []
    for number, duration in zip(numbers, part_durations):
        seek = max(duration * 0.6, 0.0)
        out_rel = f"review/frame_{number:03}.png"
        _run_ffmpeg(
            [
                "-ss",
                f"{seek:.3f}",
                "-i",
                f"parts/part_{number:03}.mp4",
                "-frames:v",
                "1",
                "-vf",
                "scale=960:-2",
                out_rel,
            ],
            cwd=work_dir,
        )
        frame_paths.append(work_dir / out_rel)
    return frame_paths


def _context_line(ctx: dict) -> str:
    return (
        f"パート{ctx.get('part')}(種別: {ctx.get('kind', '')}): "
        f"タイトル=「{ctx.get('title', '')}」 "
        f"想定ナレーション/字幕=「{ctx.get('narration', '') or ctx.get('reaction_line', '')}」"
    )


def _fixable_for(code: str, kind: str) -> bool:
    if code in _ALWAYS_FIXABLE_CODES:
        return True
    if code in _ILLUSTRATION_ONLY_FIXABLE_CODES and kind == "illustration":
        return True
    return False


async def review_parts(
    frame_paths: list[Path], part_contexts: list[dict]
) -> list[ReviewFinding] | None:
    """1回のGeminiリクエストで全パートをまとめてレビューする。

    フレーム画像とパートのテキスト情報を交互に渡し(マルチモーダル)、
    STRICTなJSON配列を要求する。パース・応答形式の異常など、いかなる例外でも
    Noneを返す(呼び出し側でstatus="skipped"に倒す)。
    fixableはGeminiの出力を信用せず、コード側のホワイトリストで機械的に決める。
    """
    try:
        client = genai.Client(
            vertexai=True,
            project=settings.GEMINI_PROJECT,
            location=settings.GEMINI_LOCATION,
        )

        contents: list[object] = [_QA_INSTRUCTIONS]
        kind_by_part: dict[int, str] = {}
        for ctx, frame_path in zip(part_contexts, frame_paths):
            part = ctx.get("part")
            if isinstance(part, int):
                kind_by_part[part] = str(ctx.get("kind", ""))
            contents.append(_context_line(ctx))
            contents.append(
                types.Part.from_bytes(data=frame_path.read_bytes(), mime_type="image/png")
            )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=REVIEW_MODEL,
            contents=contents,
        )
        text = (response.text or "").strip()

        # マークダウンのコードフェンスを剥がす(polish_narration.pyと同じ防御的パース)
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

        raw_findings = json.loads(text.strip())
        if not isinstance(raw_findings, list):
            return []

        findings: list[ReviewFinding] = []
        for item in raw_findings:
            if not isinstance(item, dict):
                continue
            part = item.get("part")
            code = item.get("code")
            description = item.get("description")
            if not isinstance(part, int):
                continue
            if not isinstance(code, str) or not code:
                continue
            if not isinstance(description, str):
                description = ""
            kind = kind_by_part.get(part, "")
            findings.append(
                ReviewFinding(
                    part=part,
                    code=code,
                    description=description,
                    fixable=_fixable_for(code, kind),
                )
            )
        return findings
    except Exception:
        logger.exception("review_parts failed; treating review as skipped")
        return None


# codeごとの機械的な修正方針(video_generator側のretake_partに渡すcompact/drop_image)。
# text_overflow/overlap → compactな再レンダリング(reuse_audio=True)。
# illustrationのcontrast/empty_slide → 生成イラストを外してダーク背景フォールバックへ。
# それ以外は警告のみで自動修正はしない。
def _fix_action(finding: ReviewFinding, kind: str) -> tuple[bool, bool] | None:
    """(compact, drop_image) を返す。修正しない場合はNone。"""
    if finding.code in _ALWAYS_FIXABLE_CODES:
        return True, False
    if finding.code in _ILLUSTRATION_ONLY_FIXABLE_CODES and kind == "illustration":
        return False, True
    return None


async def review_and_retake(
    *,
    work_dir: Path,
    part_durations: list[float],
    part_contexts: list[dict],
    retake_part: Callable[..., Awaitable[None]],
    assemble: Callable[[], Awaitable[None]],
    max_rounds: int = 2,
) -> ReviewReport:
    """フレーム抽出→レビュー→(fixableがあれば)リテイク→再アセンブルを最大max_rounds回行う。

    retake_part(part_index, *, compact, drop_image) と assemble() は
    video_generator側のクロージャで、循環importを避けるためコールバックとして渡す。
    このオーケストレーター自体がどんな例外を投げても動画生成を落としてはいけないため、
    呼び出し側(video_generator)でも丸ごとtry/exceptすること。
    """
    log = logger
    kind_by_part = {ctx["part"]: ctx.get("kind", "") for ctx in part_contexts if "part" in ctx}
    context_by_part = {ctx["part"]: ctx for ctx in part_contexts if "part" in ctx}

    all_findings: list[ReviewFinding] = []
    rounds_run = 0
    part_numbers = list(range(1, len(part_durations) + 1))
    durations_by_part = dict(zip(part_numbers, part_durations))

    pending_numbers = part_numbers
    pending_durations = part_durations

    for round_index in range(1, max_rounds + 1):
        rounds_run = round_index
        frame_paths = extract_part_frames(work_dir, pending_durations, pending_numbers)
        contexts_this_round = [context_by_part[n] for n in pending_numbers if n in context_by_part]

        log.info(
            "review round=%d extracting frames parts=%s", round_index, pending_numbers
        )
        findings = await review_parts(frame_paths, contexts_this_round)

        if findings is None:
            log.info("review round=%d gemini review unavailable; treating as skipped", round_index)
            return ReviewReport(status="skipped", rounds=round_index, findings=[])

        for finding in findings:
            log.info(
                "review round=%d part=%d code=%s fixable=%s description=%s",
                round_index,
                finding.part,
                finding.code,
                finding.fixable,
                finding.description,
            )

        all_findings = _merge_findings(all_findings, findings, pending_numbers)

        fixable_this_round = [f for f in findings if f.fixable]
        if not fixable_this_round:
            log.info("review round=%d no fixable findings; stopping", round_index)
            break

        retaken_numbers: list[int] = []
        for finding in fixable_this_round:
            kind = kind_by_part.get(finding.part, "")
            action = _fix_action(finding, kind)
            if action is None:
                continue
            compact, drop_image = action
            log.info(
                "review round=%d part=%d code=%s action=retake(compact=%s, drop_image=%s)",
                round_index,
                finding.part,
                finding.code,
                compact,
                drop_image,
            )
            await retake_part(finding.part, compact=compact, drop_image=drop_image)
            retaken_numbers.append(finding.part)

        if not retaken_numbers:
            break

        log.info("review round=%d re-assembling video after retakes=%s", round_index, retaken_numbers)
        await assemble()

        if round_index >= max_rounds:
            log.info("review reached max_rounds=%d; remaining findings become warnings", max_rounds)
            break

        # 次ラウンドはリテイクしたパートだけを再抽出・再レビューする
        pending_numbers = sorted(set(retaken_numbers))
        pending_durations = [durations_by_part[n] for n in pending_numbers]

    # 最終的に残っている(=修正されなかった/修正不能な、または直したが未検証の)findingsは
    # 警告として報告する
    status = "passed_with_warnings" if all_findings else "passed"
    report = ReviewReport(status=status, rounds=rounds_run, findings=all_findings)

    (work_dir / "review_report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _merge_findings(
    existing: list[ReviewFinding], new_round: list[ReviewFinding], reviewed_parts: list[int]
) -> list[ReviewFinding]:
    """直近ラウンドでレビュー対象になったパートの古いfindingsを新しい結果で置き換える。"""
    kept = [f for f in existing if f.part not in reviewed_parts]
    return kept + new_round

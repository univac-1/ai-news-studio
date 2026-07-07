"""video_review(自己レビュー&自動リテイク)のGemini非依存テスト。

Gemini呼び出し(genai.Client)はフェイクに差し替え、JSONパース・fixableホワイト
リスト・防御的パース(コードフェンス除去、ゴミ応答→skipped)・ReviewReportの
シリアライズ互換性を検証する。
"""

import asyncio
import json

import pytest

from app.schemas.video import ReviewFinding, ReviewReport, VideoArtifact
from app.services import video_review


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, text, exc=None):
        self._text = text
        self._exc = exc

    def generate_content(self, *, model, contents):
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._text)


class _FakeClient:
    """genai.Client の代替。コンストラクタ引数は実装と同じキーワードを受ける。"""

    response_text: str | None = None
    exc: Exception | None = None

    def __init__(self, *, vertexai=True, project="", location=""):
        self.models = _FakeModels(type(self).response_text, type(self).exc)


@pytest.fixture
def fake_gemini(monkeypatch):
    """review_partsが使うgenai.Clientをフェイクへ差し替える。テスト側で
    _FakeClient.response_text / _FakeClient.exc を設定して応答を制御する。"""
    _FakeClient.response_text = None
    _FakeClient.exc = None
    monkeypatch.setattr(video_review.genai, "Client", _FakeClient)
    return _FakeClient


def _contexts():
    return [
        {"part": 1, "kind": "hook", "title": "今週の注目", "narration": "..."},
        {"part": 2, "kind": "illustration", "title": "#1 新モデル", "narration": "..."},
        {"part": 3, "kind": "segment", "title": "#1 新モデル発表", "narration": "..."},
    ]


def _frames(tmp_path, n=3):
    paths = []
    for i in range(1, n + 1):
        p = tmp_path / f"frame_{i:03}.png"
        p.write_bytes(b"\x89PNG fake")
        paths.append(p)
    return paths


def _run_review(tmp_path):
    return asyncio.run(video_review.review_parts(_frames(tmp_path), _contexts()))


class TestReviewPartsParsing:
    def test_canned_json_is_parsed(self, fake_gemini, tmp_path):
        fake_gemini.response_text = json.dumps(
            [
                {
                    "part": 3,
                    "code": "text_overflow",
                    "description": "タイトルが右端で見切れている",
                    "severity": "high",
                }
            ],
            ensure_ascii=False,
        )
        findings = _run_review(tmp_path)
        assert findings is not None
        assert len(findings) == 1
        assert findings[0].part == 3
        assert findings[0].code == "text_overflow"
        assert findings[0].description == "タイトルが右端で見切れている"
        assert findings[0].fixable is True

    def test_empty_array_means_no_findings(self, fake_gemini, tmp_path):
        fake_gemini.response_text = "[]"
        assert _run_review(tmp_path) == []

    def test_fence_stripped_json(self, fake_gemini, tmp_path):
        fake_gemini.response_text = (
            "```json\n"
            '[{"part": 1, "code": "overlap", "description": "字幕とキャラが重なる", "severity": "medium"}]\n'
            "```"
        )
        findings = _run_review(tmp_path)
        assert findings is not None
        assert len(findings) == 1
        assert findings[0].code == "overlap"
        assert findings[0].fixable is True

    def test_garbage_response_returns_none(self, fake_gemini, tmp_path):
        fake_gemini.response_text = "ここに問題は見当たりませんでした。"
        assert _run_review(tmp_path) is None

    def test_exception_returns_none(self, fake_gemini, tmp_path):
        fake_gemini.exc = RuntimeError("quota exceeded")
        assert _run_review(tmp_path) is None

    def test_non_list_json_returns_empty(self, fake_gemini, tmp_path):
        # dictで返ってきた場合は「指摘なし」扱い(壊れてはいないがfindingsではない)
        fake_gemini.response_text = '{"findings": []}'
        assert _run_review(tmp_path) == []

    def test_malformed_items_are_dropped(self, fake_gemini, tmp_path):
        fake_gemini.response_text = json.dumps(
            [
                "not a dict",
                {"part": "three", "code": "overlap", "description": "型が不正"},
                {"part": 2, "description": "codeなし"},
                {"part": 2, "code": "contrast", "description": 123},
                {"part": 1, "code": "overlap", "description": "有効なもの"},
            ],
            ensure_ascii=False,
        )
        findings = _run_review(tmp_path)
        assert findings is not None
        # 有効なのは末尾2件(descriptionが非strのものは""に矯正されて採用)
        assert len(findings) == 2
        assert {f.part for f in findings} == {1, 2}
        by_part = {f.part: f for f in findings}
        assert by_part[2].description == ""


class TestFixableWhitelist:
    def test_text_overflow_always_fixable(self):
        assert video_review._fixable_for("text_overflow", "segment") is True
        assert video_review._fixable_for("text_overflow", "illustration") is True
        assert video_review._fixable_for("text_overflow", "hook") is True

    def test_overlap_always_fixable(self):
        assert video_review._fixable_for("overlap", "opening") is True
        assert video_review._fixable_for("overlap", "") is True

    def test_contrast_fixable_only_for_illustration(self):
        assert video_review._fixable_for("contrast", "illustration") is True
        assert video_review._fixable_for("contrast", "segment") is False
        assert video_review._fixable_for("contrast", "") is False

    def test_empty_slide_fixable_only_for_illustration(self):
        assert video_review._fixable_for("empty_slide", "illustration") is True
        assert video_review._fixable_for("empty_slide", "ranking") is False

    def test_subtitle_mismatch_never_fixable(self):
        assert video_review._fixable_for("subtitle_mismatch", "segment") is False
        assert video_review._fixable_for("subtitle_mismatch", "illustration") is False

    def test_unknown_code_never_fixable(self):
        assert video_review._fixable_for("weird_code", "illustration") is False

    def test_fixable_comes_from_whitelist_not_gemini(self, fake_gemini, tmp_path):
        # Geminiがfixable=trueを主張しても、ホワイトリスト外(segmentのcontrast)は
        # fixable=Falseに矯正される
        fake_gemini.response_text = json.dumps(
            [
                {
                    "part": 3,
                    "code": "contrast",
                    "description": "文字が読めない",
                    "severity": "high",
                    "fixable": True,
                }
            ],
            ensure_ascii=False,
        )
        findings = _run_review(tmp_path)
        assert findings is not None
        assert findings[0].fixable is False


class TestFixAction:
    def test_text_overflow_maps_to_compact(self):
        finding = ReviewFinding(part=1, code="text_overflow", description="", fixable=True)
        assert video_review._fix_action(finding, "segment") == (True, False)

    def test_illustration_contrast_maps_to_drop_image(self):
        finding = ReviewFinding(part=2, code="contrast", description="", fixable=True)
        assert video_review._fix_action(finding, "illustration") == (False, True)

    def test_non_illustration_contrast_is_warning_only(self):
        finding = ReviewFinding(part=3, code="contrast", description="", fixable=False)
        assert video_review._fix_action(finding, "segment") is None

    def test_subtitle_mismatch_is_warning_only(self):
        finding = ReviewFinding(part=3, code="subtitle_mismatch", description="", fixable=False)
        assert video_review._fix_action(finding, "segment") is None


class TestReviewReportSerialization:
    def _artifact_dict(self, **overrides):
        base = {
            "id": "20260707T000000000000Z",
            "title": "今週のAIニュースまとめ",
            "created_at": "2026-07-07T00:00:00+00:00",
            "draft_generated_at": "2026-07-06T00:00:00+00:00",
            "total_items": 3,
            "duration_seconds": 60.0,
            "video_path": "video.mp4",
            "subtitles_path": "subtitles.srt",
            "slide_count": 10,
        }
        base.update(overrides)
        return base

    def test_report_round_trip(self):
        report = ReviewReport(
            status="passed_with_warnings",
            rounds=2,
            findings=[
                ReviewFinding(
                    part=3, code="subtitle_mismatch", description="字幕が違う", fixable=False
                )
            ],
        )
        dumped = report.model_dump(mode="json")
        restored = ReviewReport(**json.loads(json.dumps(dumped, ensure_ascii=False)))
        assert restored == report

    def test_artifact_round_trip_with_review_fields(self):
        artifact = VideoArtifact(
            **self._artifact_dict(
                review_status="passed_with_warnings",
                review_findings=[
                    {
                        "part": 2,
                        "code": "contrast",
                        "description": "文字が読めない",
                        "fixable": True,
                    }
                ],
            )
        )
        # metadata.jsonへの書き込み(model_dump_json)→読み込み(VideoArtifact(**json.loads))
        # のラウンドトリップ(list_video_artifactsと同じ経路)
        restored = VideoArtifact(**json.loads(artifact.model_dump_json()))
        assert restored == artifact
        assert restored.review_findings[0].code == "contrast"
        assert restored.review_findings[0].fixable is True

    def test_old_metadata_without_review_fields_still_loads(self):
        # Feature C以前のmetadata.jsonにはreview_status/review_findingsが無い。
        # デフォルト値で読み込めること(後方互換)を保証する
        artifact = VideoArtifact(**self._artifact_dict())
        assert artifact.review_status == ""
        assert artifact.review_findings == []

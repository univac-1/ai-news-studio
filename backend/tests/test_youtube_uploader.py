"""youtube_uploader(Feature D: YouTube自動アップロード)のネットワーク非依存テスト。

googleapiclient.discovery.build と MediaFileUpload をフェイクへ差し替え、以下を検証する:
- アップロードリクエストボディの構築(title/description/tagsマッピング、タグ文字数上限)
- 冪等性ガード(二重アップロードはYouTubeAlreadyUploadedError)
- アップロード前のpublishはYouTubeNotUploadedError
- metadata.jsonの更新ラウンドトリップ
- サムネイル設定失敗はアップロード自体を失敗させない
"""

import json

import pytest

from app.services import youtube_uploader as yt


# ---------------------------------------------------------------------------
# フェイクのgoogleapiclient
# ---------------------------------------------------------------------------


class _FakeMediaFileUpload:
    def __init__(self, path, chunksize=-1, resumable=False, mimetype=None):
        self.path = path
        self.mimetype = mimetype


class _FakeUploadRequest:
    def __init__(self, body, response_id, exc=None):
        self.body = body
        self._response_id = response_id
        self._exc = exc
        self._done = False

    def next_chunk(self):
        if self._exc is not None:
            raise self._exc
        self._done = True
        return None, {"id": self._response_id}


class _FakeThumbnailRequest:
    def __init__(self, exc=None):
        self._exc = exc

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return {}


class _FakeUpdateRequest:
    def __init__(self, body):
        self.body = body

    def execute(self):
        return {}


class _FakeVideosResource:
    def __init__(self, client):
        self._client = client

    def insert(self, part, body, media_body):
        self._client.insert_calls.append({"part": part, "body": body, "media_body": media_body})
        return _FakeUploadRequest(
            body, self._client.upload_video_id, exc=self._client.upload_exc
        )

    def update(self, part, body):
        self._client.update_calls.append({"part": part, "body": body})
        return _FakeUpdateRequest(body)


class _FakeThumbnailsResource:
    def __init__(self, client):
        self._client = client

    def set(self, videoId, media_body):
        self._client.thumbnail_calls.append({"videoId": videoId, "media_body": media_body})
        return _FakeThumbnailRequest(exc=self._client.thumbnail_exc)


class _FakeYouTubeClient:
    def __init__(self):
        self.insert_calls = []
        self.update_calls = []
        self.thumbnail_calls = []
        self.upload_video_id = "yt-video-123"
        self.upload_exc = None
        self.thumbnail_exc = None

    def videos(self):
        return _FakeVideosResource(self)

    def thumbnails(self):
        return _FakeThumbnailsResource(self)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeYouTubeClient()
    monkeypatch.setattr(yt, "build", lambda *args, **kwargs: client)
    monkeypatch.setattr(yt, "MediaFileUpload", _FakeMediaFileUpload)
    return client


@pytest.fixture
def configured_settings(monkeypatch):
    monkeypatch.setattr(yt.settings, "YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setattr(yt.settings, "YOUTUBE_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(yt.settings, "YOUTUBE_REFRESH_TOKEN", "rtoken")


# ---------------------------------------------------------------------------
# アーティファクトディレクトリのセットアップ
# ---------------------------------------------------------------------------


def _make_artifact_dir(tmp_path, monkeypatch, video_id="20260706T000000000000Z", **metadata_overrides):
    monkeypatch.setattr(yt, "GENERATED_DIR", tmp_path)
    work_dir = tmp_path / video_id
    work_dir.mkdir(parents=True)
    (work_dir / "video.mp4").write_bytes(b"fake mp4 bytes")
    (work_dir / "thumbnail.png").write_bytes(b"fake png bytes")

    metadata = {
        "id": video_id,
        "title": "今週のAIニュースまとめ",
        "created_at": "2026-07-06T00:00:00+00:00",
        "draft_generated_at": "2026-07-06T00:00:00+00:00",
        "total_items": 3,
        "duration_seconds": 60.0,
        "video_path": "video.mp4",
        "subtitles_path": "subtitles.srt",
        "slide_count": 5,
        "thumbnail_path": "thumbnail.png",
        "youtube_description": "今週のAIニュースまとめの説明文です。",
        "hashtags": ["#AIニュース", "#AI速報", "#人工知能"],
    }
    metadata.update(metadata_overrides)
    (work_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return video_id, work_dir


# ---------------------------------------------------------------------------
# _build_tags
# ---------------------------------------------------------------------------


class TestBuildTags:
    def test_strips_leading_hash(self):
        assert yt._build_tags(["#AI", "#ニュース"]) == ["AI", "ニュース"]

    def test_drops_empty_tags(self):
        assert yt._build_tags(["#AI", "", "#  ", "#B"]) == ["AI", "B"]

    def test_caps_total_length_under_limit(self):
        # 1つ499文字のタグの後に短いタグを足すと合計500文字を超えるため、
        # 超過するタグ以降は採用しない
        long_tag = "#" + ("a" * 499)
        hashtags = [long_tag, "#extra"]
        tags = yt._build_tags(hashtags)
        assert tags == ["a" * 499]
        total_len = len(tags[0])
        assert total_len < yt.YOUTUBE_TAGS_CHAR_LIMIT

    def test_many_short_tags_stay_under_limit(self):
        hashtags = [f"#tag{i}" for i in range(200)]
        tags = yt._build_tags(hashtags)
        total_len = sum(len(t) for t in tags) + max(len(tags) - 1, 0)
        assert total_len < yt.YOUTUBE_TAGS_CHAR_LIMIT


# ---------------------------------------------------------------------------
# upload_video
# ---------------------------------------------------------------------------


class TestUploadVideo:
    def test_missing_config_raises(self, tmp_path, monkeypatch, fake_client):
        video_id, _ = _make_artifact_dir(tmp_path, monkeypatch)
        monkeypatch.setattr(yt.settings, "YOUTUBE_CLIENT_ID", "")
        monkeypatch.setattr(yt.settings, "YOUTUBE_CLIENT_SECRET", "")
        monkeypatch.setattr(yt.settings, "YOUTUBE_REFRESH_TOKEN", "")
        with pytest.raises(yt.YouTubeConfigError):
            yt.upload_video(video_id)

    def test_missing_video_file_raises(self, tmp_path, monkeypatch, configured_settings, fake_client):
        video_id, work_dir = _make_artifact_dir(tmp_path, monkeypatch)
        (work_dir / "video.mp4").unlink()
        with pytest.raises(FileNotFoundError):
            yt.upload_video(video_id)

    def test_request_body_maps_title_description_tags(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        video_id, _ = _make_artifact_dir(tmp_path, monkeypatch)
        result = yt.upload_video(video_id)

        assert len(fake_client.insert_calls) == 1
        call = fake_client.insert_calls[0]
        assert call["part"] == "snippet,status"
        assert call["body"]["snippet"]["title"] == "今週のAIニュースまとめ"
        assert call["body"]["snippet"]["description"] == "今週のAIニュースまとめの説明文です。"
        assert call["body"]["snippet"]["tags"] == ["AIニュース", "AI速報", "人工知能"]
        assert call["body"]["snippet"]["categoryId"] == "28"
        assert call["body"]["status"]["privacyStatus"] == "unlisted"
        assert call["body"]["status"]["selfDeclaredMadeForKids"] is False

        assert result == {
            "youtube_video_id": "yt-video-123",
            "youtube_privacy": "unlisted",
            "youtube_url": "https://youtu.be/yt-video-123",
        }

    def test_metadata_json_round_trip_after_upload(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        video_id, work_dir = _make_artifact_dir(tmp_path, monkeypatch)
        yt.upload_video(video_id)

        saved = json.loads((work_dir / "metadata.json").read_text(encoding="utf-8"))
        assert saved["youtube_video_id"] == "yt-video-123"
        assert saved["youtube_privacy"] == "unlisted"
        assert saved["youtube_url"] == "https://youtu.be/yt-video-123"
        # 既存フィールドは保持されていること
        assert saved["title"] == "今週のAIニュースまとめ"

    def test_double_upload_raises_already_uploaded(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        video_id, _ = _make_artifact_dir(tmp_path, monkeypatch)
        yt.upload_video(video_id)
        with pytest.raises(yt.YouTubeAlreadyUploadedError):
            yt.upload_video(video_id)
        # 2回目はYouTube APIを叩いていない
        assert len(fake_client.insert_calls) == 1

    def test_thumbnail_failure_does_not_fail_upload(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        fake_client.thumbnail_exc = RuntimeError("channel not verified")
        video_id, work_dir = _make_artifact_dir(tmp_path, monkeypatch)
        result = yt.upload_video(video_id)

        assert result["youtube_video_id"] == "yt-video-123"
        saved = json.loads((work_dir / "metadata.json").read_text(encoding="utf-8"))
        assert saved["youtube_video_id"] == "yt-video-123"
        assert len(fake_client.thumbnail_calls) == 1

    def test_missing_thumbnail_file_skips_thumbnail_call(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        video_id, work_dir = _make_artifact_dir(tmp_path, monkeypatch)
        (work_dir / "thumbnail.png").unlink()
        yt.upload_video(video_id)
        assert fake_client.thumbnail_calls == []


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------


class TestPublishVideo:
    def test_publish_without_upload_raises(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        video_id, _ = _make_artifact_dir(tmp_path, monkeypatch)
        with pytest.raises(yt.YouTubeNotUploadedError):
            yt.publish_video(video_id)
        assert fake_client.update_calls == []

    def test_publish_after_upload_updates_privacy(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        video_id, work_dir = _make_artifact_dir(tmp_path, monkeypatch)
        yt.upload_video(video_id)
        result = yt.publish_video(video_id)

        assert result == {
            "youtube_video_id": "yt-video-123",
            "youtube_privacy": "public",
            "youtube_url": "https://youtu.be/yt-video-123",
        }
        assert len(fake_client.update_calls) == 1
        call = fake_client.update_calls[0]
        assert call["body"]["id"] == "yt-video-123"
        assert call["body"]["status"]["privacyStatus"] == "public"

        saved = json.loads((work_dir / "metadata.json").read_text(encoding="utf-8"))
        assert saved["youtube_privacy"] == "public"

    def test_double_publish_raises_already_published(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        video_id, _ = _make_artifact_dir(tmp_path, monkeypatch)
        yt.upload_video(video_id)
        yt.publish_video(video_id)
        with pytest.raises(yt.YouTubeAlreadyPublishedError):
            yt.publish_video(video_id)
        assert len(fake_client.update_calls) == 1

    def test_publish_missing_video_raises_file_not_found(
        self, tmp_path, monkeypatch, configured_settings, fake_client
    ):
        monkeypatch.setattr(yt, "GENERATED_DIR", tmp_path)
        with pytest.raises(FileNotFoundError):
            yt.publish_video("does-not-exist")

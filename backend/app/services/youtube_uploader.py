"""YouTube自動アップロード(Feature D: 週次完全自律運転)。

生成済み動画を「限定公開(unlisted)」でYouTubeへアップロードし、人間が内容を
確認したうえで `publish_video` を呼んで「公開(public)」に切り替える運用を想定する。

OAuth2はリフレッシュトークン方式(google.oauth2.credentials.Credentials)を使う。
リフレッシュトークンの取得方法は docs/youtube-oauth.md と
scripts/get_youtube_refresh_token.py を参照。

ここでの関数はすべて同期(googleapiclientが同期APIのため)。呼び出し側は
`asyncio.to_thread` 経由で呼ぶこと。
"""

import json
import logging
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from ..core.config import settings
from .video_generator import GENERATED_DIR

logger = logging.getLogger(__name__)

# https://developers.google.com/youtube/v3/docs/videos#snippet.tags[]
# tags[]は全タグの合計文字数(カンマ区切りを含む)が500文字を超えてはならない
YOUTUBE_TAGS_CHAR_LIMIT = 500


class YouTubeUploadError(RuntimeError):
    """YouTubeアップロード関連のエラー基底クラス。"""


class YouTubeConfigError(YouTubeUploadError):
    """OAuth2設定(client_id/client_secret/refresh_token)が不足している。"""


class YouTubeAlreadyUploadedError(YouTubeUploadError):
    """既にYouTubeへアップロード済み(冪等性ガード)。"""


class YouTubeNotUploadedError(YouTubeUploadError):
    """まだYouTubeへアップロードされていないため公開できない。"""


class YouTubeAlreadyPublishedError(YouTubeUploadError):
    """既に公開(public)済み。"""


def _build_youtube_client():
    if not (
        settings.YOUTUBE_CLIENT_ID
        and settings.YOUTUBE_CLIENT_SECRET
        and settings.YOUTUBE_REFRESH_TOKEN
    ):
        raise YouTubeConfigError(
            "YouTubeアップロードの設定が不足しています。backend/.env に "
            "YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN を"
            "設定してください(取得手順: docs/youtube-oauth.md)。"
        )
    credentials = Credentials(
        token=None,
        refresh_token=settings.YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.YOUTUBE_CLIENT_ID,
        client_secret=settings.YOUTUBE_CLIENT_SECRET,
    )
    return build("youtube", "v3", credentials=credentials)


def _artifact_dir(video_id: str) -> Path:
    return GENERATED_DIR / video_id


def _metadata_path(video_id: str) -> Path:
    return _artifact_dir(video_id) / "metadata.json"


def _read_metadata(video_id: str) -> dict:
    path = _metadata_path(video_id)
    if not path.exists():
        raise FileNotFoundError(f"動画が見つかりません: {video_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_metadata(video_id: str, metadata: dict) -> None:
    _metadata_path(video_id).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _build_tags(hashtags: list[str]) -> list[str]:
    """#付きハッシュタグをYouTubeのtags[]形式(#なし)に変換し、合計文字数を
    YOUTUBE_TAGS_CHAR_LIMIT未満に収まるまで先頭から採用する。"""
    tags: list[str] = []
    total_len = 0
    for raw in hashtags:
        cleaned = raw.lstrip("#").strip()
        if not cleaned:
            continue
        # YouTube側はカンマ区切りの合計として数えるため、2件目以降は区切り文字分も見込む
        added_len = len(cleaned) + (1 if tags else 0)
        if total_len + added_len >= YOUTUBE_TAGS_CHAR_LIMIT:
            break
        tags.append(cleaned)
        total_len += added_len
    return tags


def upload_video(video_id: str) -> dict:
    """動画を「限定公開」でYouTubeへアップロードし、metadata.jsonを更新する。

    既にアップロード済み(metadata.jsonにyoutube_video_idがある)場合は
    YouTubeAlreadyUploadedErrorを送出する(冪等性ガード)。
    サムネイル設定の失敗はアップロード自体を失敗させない(警告ログのみ)。
    """
    metadata = _read_metadata(video_id)
    if metadata.get("youtube_video_id"):
        raise YouTubeAlreadyUploadedError(
            f"既にYouTubeへアップロード済みです(video_id={video_id}, "
            f"youtube_video_id={metadata['youtube_video_id']})"
        )

    video_path = _artifact_dir(video_id) / "video.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"video.mp4が見つかりません: {video_path}")

    youtube = _build_youtube_client()

    tags = _build_tags(metadata.get("hashtags", []))
    body = {
        "snippet": {
            "title": metadata.get("title", ""),
            "description": metadata.get("youtube_description", ""),
            "tags": tags,
            "categoryId": "28",  # Science & Technology
        },
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _status, response = request.next_chunk()

    youtube_video_id = response["id"]

    thumbnail_path = _artifact_dir(video_id) / "thumbnail.png"
    if thumbnail_path.exists():
        try:
            youtube.thumbnails().set(
                videoId=youtube_video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png"),
            ).execute()
        except Exception:
            # カスタムサムネイル設定はチャンネル確認(電話番号確認)が必要なため、
            # 未確認チャンネルでは403になりうる。動画本体のアップロードは成功しているので
            # ここでは握りつぶして警告ログのみ残す。
            logger.exception(
                "YouTubeサムネイル設定に失敗しました(動画本体のアップロードは成功): video_id=%s",
                video_id,
            )

    metadata["youtube_video_id"] = youtube_video_id
    metadata["youtube_privacy"] = "unlisted"
    metadata["youtube_url"] = f"https://youtu.be/{youtube_video_id}"
    _write_metadata(video_id, metadata)

    return {
        "youtube_video_id": youtube_video_id,
        "youtube_privacy": metadata["youtube_privacy"],
        "youtube_url": metadata["youtube_url"],
    }


def publish_video(video_id: str) -> dict:
    """限定公開でアップロード済みの動画を「公開」に切り替え、metadata.jsonを更新する。"""
    metadata = _read_metadata(video_id)
    youtube_video_id = metadata.get("youtube_video_id")
    if not youtube_video_id:
        raise YouTubeNotUploadedError(
            f"まだYouTubeへアップロードされていません(video_id={video_id})。"
            "先に /upload-youtube を実行してください。"
        )
    if metadata.get("youtube_privacy") == "public":
        raise YouTubeAlreadyPublishedError(
            f"既に公開済みです(video_id={video_id}, youtube_video_id={youtube_video_id})"
        )

    youtube = _build_youtube_client()
    youtube.videos().update(
        part="status",
        body={
            "id": youtube_video_id,
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        },
    ).execute()

    metadata["youtube_privacy"] = "public"
    _write_metadata(video_id, metadata)

    return {
        "youtube_video_id": youtube_video_id,
        "youtube_privacy": metadata["youtube_privacy"],
        "youtube_url": metadata.get("youtube_url", f"https://youtu.be/{youtube_video_id}"),
    }

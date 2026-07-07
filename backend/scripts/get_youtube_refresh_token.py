"""YouTubeアップロード用のOAuth2リフレッシュトークンを取得するワンショットスクリプト。

事前準備(詳細は docs/youtube-oauth.md 参照):
    1. Google Cloud Console で「デスクトップアプリ」種別のOAuthクライアントを作成する
    2. YouTube Data API v3 を有効化する

使い方:
    cd backend
    uv run python scripts/get_youtube_refresh_token.py <CLIENT_ID> <CLIENT_SECRET>

    または環境変数から読む場合:
    set YOUTUBE_CLIENT_ID=xxxx
    set YOUTUBE_CLIENT_SECRET=yyyy
    uv run python scripts/get_youtube_refresh_token.py

実行するとブラウザが開き、Googleアカウントでの認可(YouTubeのアップロード・管理権限)を
求められる。認可後、コンソールにリフレッシュトークンが表示されるので、
backend/.env の YOUTUBE_REFRESH_TOKEN にコピーすること。

注意: Google Cloud のOAuth同意画面が「テストモード(未検証アプリ)」のままだと、
発行されたリフレッシュトークンは7日間で失効する。継続運用する場合はアプリを
本番公開(または内部利用アプリとして検証不要な設定)にすること。
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def _resolve_client_credentials() -> tuple[str, str]:
    client_id = os.environ.get("YOUTUBE_CLIENT_ID") or (sys.argv[1] if len(sys.argv) > 1 else "")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET") or (
        sys.argv[2] if len(sys.argv) > 2 else ""
    )
    if not client_id or not client_secret:
        print(__doc__)
        print(
            "エラー: CLIENT_ID / CLIENT_SECRET が指定されていません"
            "(引数、または環境変数 YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET で指定してください)。"
        )
        sys.exit(1)
    return client_id, client_secret


def main() -> None:
    client_id, client_secret = _resolve_client_credentials()

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    credentials = flow.run_local_server(port=0)

    print()
    print("=" * 60)
    print("認可に成功しました。以下をコピーして backend/.env に設定してください:")
    print()
    print(f"YOUTUBE_CLIENT_ID={client_id}")
    print(f"YOUTUBE_CLIENT_SECRET={client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN={credentials.refresh_token}")
    print()
    print("注意: OAuth同意画面がテストモード(未検証アプリ)の場合、このリフレッシュ")
    print("トークンは発行から7日で失効します。継続運用するにはアプリを本番公開するか、")
    print("組織内限定アプリとして検証不要な設定にしてください。詳細は docs/youtube-oauth.md 参照。")
    print("=" * 60)


if __name__ == "__main__":
    main()

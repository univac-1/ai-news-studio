# YouTube 自動アップロード(Feature D: 週次完全自律運転)セットアップ

週次で生成した動画を自動的に YouTube へ「限定公開」でアップロードし、人間が内容を確認したうえで「公開する」操作を行う運用のためのセットアップ手順です。

## 1. Google Cloud プロジェクトの準備

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成(または既存のものを利用)する。
2. 「APIとサービス」→「有効なAPIとサービス」から **YouTube Data API v3** を有効化する。
3. 「APIとサービス」→「OAuth同意画面」を設定する。
   - ユーザータイプは通常「外部」を選択(組織アカウントのみで使うなら「内部」でも可)。
   - スコープには以下を追加:
     - `https://www.googleapis.com/auth/youtube.upload`
     - `https://www.googleapis.com/auth/youtube`
   - テストユーザーに、アップロード先のYouTubeチャンネルを持つGoogleアカウントを追加する。

## 2. OAuth クライアント(デスクトップアプリ)の作成

1. 「APIとサービス」→「認証情報」→「認証情報を作成」→「OAuthクライアントID」を選択する。
2. アプリケーションの種類は **「デスクトップアプリ」** を選択する。
3. 作成後に表示される **クライアントID** と **クライアントシークレット** を控えておく。

## 3. リフレッシュトークンの取得

`backend/scripts/get_youtube_refresh_token.py` を使ってワンショットで取得します。

```bash
cd backend
uv run python scripts/get_youtube_refresh_token.py <CLIENT_ID> <CLIENT_SECRET>
```

もしくは環境変数で渡す場合:

```bash
cd backend
set YOUTUBE_CLIENT_ID=xxxx
set YOUTUBE_CLIENT_SECRET=yyyy
uv run python scripts/get_youtube_refresh_token.py
```

実行するとブラウザが開き、アップロード先チャンネルを持つGoogleアカウントでログイン・権限承認を行います。完了するとコンソールに `YOUTUBE_REFRESH_TOKEN` が出力されるので、以下を `backend/.env` に設定してください。

```
YOUTUBE_CLIENT_ID=xxxx
YOUTUBE_CLIENT_SECRET=yyyy
YOUTUBE_REFRESH_TOKEN=zzzz
YOUTUBE_UPLOAD_ENABLED=true
```

### ⚠️ テストモード(未検証アプリ)の注意

OAuth同意画面が「テストモード(未検証アプリ)」のままの場合、**発行されたリフレッシュトークンは7日間で失効します**。週次バッチを継続運用する場合は、以下のいずれかが必要です。

- OAuth同意画面を「本番公開」する(Googleの審査が必要になる場合があります)。
- 「内部」ユーザータイプ(Google Workspace組織限定)で運用し、検証プロセスを回避する。
- 7日以内に `get_youtube_refresh_token.py` を再実行してトークンを更新し続ける(暫定運用向け)。

## 4. YouTube API クォータ

YouTube Data API v3 のデフォルトクォータは **1日あたり 10,000 units** です。動画アップロード(`videos.insert`)は**1回あたり 1,600 units** を消費するため、デフォルトクォータでは1日あたり最大6本程度のアップロードが可能です。週次運用(週1本)であれば十分ですが、リトライを重ねるとすぐに消費するため注意してください。

- サムネイル設定(`thumbnails.set`)は別途 50 units 程度を消費します。
- クォータ増加が必要な場合は Google Cloud Console の「APIとサービス」→「割り当て」から申請します。

## 5. Cloud Scheduler での週次実行例

Cloud Run 等にデプロイしたバックエンドに対して、Cloud Scheduler から Basic 認証付きで `POST /api/videos/generate-weekly-from-new-draft` を叩く設定例です。

```bash
BASIC_AUTH=$(echo -n "admin:change-me" | base64)

gcloud scheduler jobs create http weekly-ai-news-video \
  --schedule="0 9 * * 1" \
  --uri="https://<cloud-run-url>/api/videos/generate-weekly-from-new-draft" \
  --http-method=POST \
  --headers="Authorization=Basic ${BASIC_AUTH}" \
  --attempt-deadline=1800s \
  --max-retry-attempts=0 \
  --time-zone="Asia/Tokyo"
```

動画生成(VOICEVOX合成・画像生成・FFmpeg合成・自己レビュー・YouTubeアップロード)は数分〜数十分かかるため、Cloud Run 側のリクエストタイムアウトも延長しておく必要があります。

```bash
gcloud run services update <service-name> \
  --timeout=3600
```

ポイント:

- `--attempt-deadline=1800s`: Cloud Scheduler 側のリクエストタイムアウト(30分)。
- `--max-retry-attempts=0`: 動画生成は冪等ではない(実行するたびに新しい動画が生成される)ため、失敗時の自動リトライは無効にする。
- `gcloud run services update --timeout=3600`: Cloud Run 側のリクエストタイムアウトを60分に延長し、長時間の生成処理がタイムアウトで打ち切られないようにする。

## 6. 承認フロー(限定公開 → 人間が「公開する」)

1. `YOUTUBE_UPLOAD_ENABLED=true` の状態で週次生成が走ると、動画生成完了後に自動で `upload-youtube` が呼ばれ、YouTubeへ **限定公開(unlisted)** でアップロードされる。
2. 人間が動画一覧(フロントエンド)またはAPI (`GET /api/videos`) で内容を確認する。
   - `metadata.json` の `youtube_url` に限定公開URLが記録される。
3. 問題なければ `POST /api/videos/{video_id}/publish` を呼び、「公開(public)」に切り替える。
4. アップロードが自動チェーンで失敗した場合(クォータ超過・認証切れ等)は、バックエンドのログにエラーが記録される。手動で `POST /api/videos/{video_id}/upload-youtube` を再実行することでリカバリできる(既にアップロード済みの場合は409が返る)。

この「限定公開でアップロード → 人間が最終承認して公開」という二段階により、AIが生成した動画がノーチェックで一般公開されることを防ぎます。

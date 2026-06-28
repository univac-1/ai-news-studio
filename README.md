# AI News Studio

AIニュース動画制作管理ツール。直近1週間の優先度Aニュースを自動収集し、週次動画ドラフトをワンクリックで生成します。

## 構成

```
ai-news-studio/
  frontend/      # Vite + React + TypeScript フロントエンド
  backend/       # Python / FastAPI バックエンド
```

---

## バックエンド起動

### 1. 環境変数

```bash
cp backend/.env.example backend/.env
# backend/.env を編集（BASIC_AUTH_PASSWORD 等）
```

### 2. 依存インストール & 起動

```bash
cd backend
uv sync --python 3.11 --system-certs
uv run uvicorn app.main:app --reload --port 8000
```

`http://localhost:8000/api/health` にアクセスし Basic 認証ダイアログが出れば OK。

---

## フロントエンド起動

### 1. 環境変数

```bash
cp frontend/.env.example frontend/.env.local
# frontend/.env.local の API_USERNAME / API_PASSWORD を backend/.env と合わせる
```

### 2. 依存インストール & 起動

```bash
cd frontend
npm install
npm run dev
```

`http://localhost:5173` でアクセス。Vite のプロキシが `/api/*` を FastAPI に転送し、Basic 認証ヘッダーをサーバー側で付与します（ブラウザに認証情報は露出しません）。

---

## API エンドポイント

| Method | Path | 説明 |
|--------|------|------|
| GET | /api/health | ヘルスチェック |
| GET | /api/news/weekly | 直近7日の全ニュース |
| GET | /api/news/priority-a | 優先度A・使用済み除外 |
| POST | /api/drafts/generate-weekly | 週次ドラフト生成 |
| GET | /api/drafts/latest | 最新ドラフト取得 |
| GET | /api/used-news | 使用済みニュース一覧 |
| POST | /api/used-news | 使用済みとして記録 |

全エンドポイントに Basic 認証が必要です。

---

## データ永続化

使用済みニュースと最新ドラフトは `backend/data/` に JSON ファイルとして保存されます（`.gitignore` 対象）。

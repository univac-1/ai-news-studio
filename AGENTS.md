# AGENTS.md

このリポジトリは、AI ニュースの収集と週次動画ドラフト作成を行う AI News Studio です。

## プロジェクト構成

- `frontend/`: Vite + React + TypeScript のフロントエンド。
- `backend/`: Python 3.11 + FastAPI のバックエンド。
- `backend/data/`: 使用済みニュースや最新ドラフトなどの実行時 JSON データ。Git 管理対象外です。

## 開発コマンド

バックエンド:

```bash
cd backend
uv sync --python 3.11 --system-certs
uv run uvicorn app.main:app --reload --port 8000
```

フロントエンド:

```bash
cd frontend
npm install
npm run dev
```

フロントエンドのビルド確認:

```bash
cd frontend
npm run build
```

## ローカルサーバー起動の扱い

- ユーザーから明示的な依頼がない限り、`npm run dev`、`npm run preview`、`vite`、`uvicorn` など localhost を使う開発サーバーやプレビューサーバーを勝手に起動しないでください。
- 動作確認にローカルサーバーが必要な場合は、起動前にユーザーへ確認してください。
- ビルド、型チェック、静的な確認で代替できる場合は、まずそれを優先してください。

## 環境ファイル

- バックエンドの環境変数が必要な場合は、`backend/.env.example` を `backend/.env` にコピーします。
- フロントエンドの環境変数が必要な場合は、`frontend/.env.example` を `frontend/.env.local` にコピーします。
- `.env`、`.env.local`、認証情報、API キー、生成された実行時データはコミットしないでください。

## コーディング方針

- 変更は依頼された範囲に絞ってください。
- 新しい抽象化を導入する前に、既存の実装パターンを優先してください。
- 型定義、スキーマ、サービス層など、既存の責務分離に合わせて変更してください。
- フロントエンド UI は既存の React、Tailwind CSS、Radix UI、lucide-react の構成に合わせてください。
- バックエンド API のレスポンス形状を変える場合は、スキーマとサービス実装も合わせて更新してください。
- 明示的に必要な場合を除き、`backend/data/` 配下の実行時データは変更しないでください。

## 検証

- フロントエンド変更後は、可能であれば `frontend/` で `npm run build` を実行してください。
- バックエンド変更では、専用のテストコマンドがない場合、起動が必要な確認はユーザーの許可を得てから行ってください。
- 実行できなかった検証がある場合は、最終報告で理由を明記してください。

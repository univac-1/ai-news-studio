import { NewsItem } from '@/types/news'
import {
  UsedNewsEntry,
  VideoArtifact,
  VideoArtifactList,
  VideoGenerationResult,
  VideoPlanDraft,
} from '@/types/draft'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  getWeeklyNews: () => req<NewsItem[]>('/api/news/weekly'),
  getPriorityANews: () => req<NewsItem[]>('/api/news/priority-a'),
  generateDraft: () => req<VideoPlanDraft>('/api/drafts/generate-weekly', { method: 'POST' }),
  getLatestDraft: () => req<VideoPlanDraft | null>('/api/drafts/latest'),
  getUsedNews: () => req<UsedNewsEntry[]>('/api/used-news'),
  markAsUsed: (newsId: string) =>
    req<{ success: boolean }>('/api/used-news', {
      method: 'POST',
      body: JSON.stringify({ news_id: newsId }),
    }),
  generateVideoFromLatest: () =>
    req<VideoArtifact>('/api/videos/generate-from-latest', { method: 'POST' }),
  generateWeeklyDraftAndVideo: () =>
    req<VideoGenerationResult>('/api/videos/generate-weekly-from-new-draft', { method: 'POST' }),
  getVideos: () => req<VideoArtifactList>('/api/videos'),
  downloadVideo: async (videoId: string): Promise<void> => {
    const res = await fetch(`${BASE}/api/videos/${videoId}/download`)
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText)
      throw new Error(`${res.status}: ${text}`)
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `ai-news-studio-${videoId}.mp4`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
  downloadThumbnail: async (videoId: string): Promise<void> => {
    const res = await fetch(`${BASE}/api/videos/${videoId}/thumbnail`)
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText)
      throw new Error(`${res.status}: ${text}`)
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `ai-news-studio-${videoId}.png`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
  uploadToYouTube: (videoId: string) =>
    req<VideoArtifact>(`/api/videos/${videoId}/upload-youtube`, { method: 'POST' }),
  publishVideo: (videoId: string) =>
    req<VideoArtifact>(`/api/videos/${videoId}/publish`, { method: 'POST' }),
}

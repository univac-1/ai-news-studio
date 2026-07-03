import { NewsItem } from '@/types/news'
import { UsedNewsEntry, VideoArtifact, VideoArtifactList, VideoPlanDraft } from '@/types/draft'

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
  getVideos: () => req<VideoArtifactList>('/api/videos'),
  getVideoDownloadUrl: (videoId: string) => `${BASE}/api/videos/${videoId}/download`,
  getVideoThumbnailUrl: (videoId: string) => `${BASE}/api/videos/${videoId}/thumbnail`,
}

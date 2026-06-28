import { useState, useEffect } from 'react'
import { NewsItem } from '@/types/news'
import { isWithinLastNDays } from '@/lib/utils'

const NEWS_URL =
  'https://storage.googleapis.com/ai-watch-feed-llm-watch-public/data/models.json'

interface UseNewsDataResult {
  news: NewsItem[]
  loading: boolean
  error: string | null
}

export function useNewsData(): UseNewsDataResult {
  const [news, setNews] = useState<NewsItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function fetchNews() {
      try {
        const res = await fetch(NEWS_URL)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: NewsItem[] = await res.json()

        if (cancelled) return

        const filtered = data
          .filter(item => isWithinLastNDays(item.published_at, 7))
          .sort((a, b) => {
            if (a.importance !== b.importance) {
              return a.importance === 'A' ? -1 : 1
            }
            return (
              new Date(b.published_at).getTime() -
              new Date(a.published_at).getTime()
            )
          })

        setNews(filtered)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'データの取得に失敗しました')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchNews()
    return () => {
      cancelled = true
    }
  }, [])

  return { news, loading, error }
}

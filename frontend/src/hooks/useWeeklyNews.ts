import { useEffect, useState } from 'react'
import { NewsItem } from '@/types/news'
import { api } from '@/lib/api'

export function useWeeklyNews() {
  const [news, setNews] = useState<NewsItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getWeeklyNews()
      .then(setNews)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return { news, loading, error }
}

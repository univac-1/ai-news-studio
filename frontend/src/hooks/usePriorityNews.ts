import { useCallback, useEffect, useState } from 'react'
import { NewsItem } from '@/types/news'
import { api } from '@/lib/api'

export function usePriorityNews() {
  const [news, setNews] = useState<NewsItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api.getPriorityANews()
      .then(setNews)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  return { news, loading, error, refetch: load }
}

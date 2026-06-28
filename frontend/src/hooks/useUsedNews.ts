import { useCallback, useEffect, useState } from 'react'
import { UsedNewsEntry } from '@/types/draft'
import { api } from '@/lib/api'

export function useUsedNews() {
  const [usedNews, setUsedNews] = useState<UsedNewsEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api.getUsedNews()
      .then(setUsedNews)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  return { usedNews, loading, error, refetch: load }
}

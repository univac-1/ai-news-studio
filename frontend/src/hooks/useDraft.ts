import { useCallback, useEffect, useState } from 'react'
import { VideoPlanDraft } from '@/types/draft'
import { api } from '@/lib/api'

export function useDraft() {
  const [draft, setDraft] = useState<VideoPlanDraft | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getLatestDraft()
      .then(d => setDraft(d))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const generate = useCallback(async () => {
    setGenerating(true)
    setError(null)
    try {
      const d = await api.generateDraft()
      setDraft(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : '生成に失敗しました')
    } finally {
      setGenerating(false)
    }
  }, [])

  return { draft, loading, generating, error, generate }
}

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
      return d
    } catch (e) {
      setError(e instanceof Error ? e.message : '生成に失敗しました')
      return null
    } finally {
      setGenerating(false)
    }
  }, [])

  const replaceDraft = useCallback((nextDraft: VideoPlanDraft) => {
    setDraft(nextDraft)
    setError(null)
  }, [])

  return { draft, loading, generating, error, generate, replaceDraft }
}

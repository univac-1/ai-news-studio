import { useCallback, useEffect, useState } from 'react'
import { VideoArtifact } from '@/types/draft'
import { api } from '@/lib/api'

export function useVideos() {
  const [videos, setVideos] = useState<VideoArtifact[]>([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(async () => {
    setError(null)
    const res = await api.getVideos()
    setVideos(res.items)
  }, [])

  useEffect(() => {
    refetch()
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [refetch])

  const generate = useCallback(async () => {
    setGenerating(true)
    setError(null)
    try {
      const video = await api.generateVideoFromLatest()
      setVideos(prev => [video, ...prev.filter(item => item.id !== video.id)])
    } catch (e) {
      setError(e instanceof Error ? e.message : '動画生成に失敗しました')
    } finally {
      setGenerating(false)
    }
  }, [])

  return { videos, loading, generating, error, generate, refetch }
}

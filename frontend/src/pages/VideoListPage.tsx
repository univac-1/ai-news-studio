import { useState } from 'react'
import { Download, FileText, Image, Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { CopyButton } from '@/components/CopyButton'
import { useVideos } from '@/hooks/useVideos'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'

function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  return minutes > 0 ? `${minutes}分${rest}秒` : `${rest}秒`
}

export function VideoListPage() {
  const { videos, loading, error } = useVideos()
  const [actionError, setActionError] = useState<string | null>(null)

  async function runDownload(action: () => Promise<void>) {
    setActionError(null)
    try {
      await action()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'ダウンロードに失敗しました')
    }
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-4 max-w-5xl mx-auto">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">動画一覧</h2>
            <p className="text-xs text-muted-foreground mt-1">
              生成済み動画を確認し、MP4・サムネイル・概要欄を取り出します
            </p>
          </div>
          {!loading && <span className="text-sm text-muted-foreground">{videos.length}件</span>}
        </div>

        {(error || actionError) && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {actionError || error}
          </div>
        )}

        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full rounded-lg" />
            ))}
          </div>
        ) : videos.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
            <Video className="w-8 h-8 opacity-30" />
            <p className="text-sm">生成済み動画はまだありません</p>
          </div>
        ) : (
          <div className="space-y-2">
            {videos.map(video => (
              <Card key={video.id}>
                <CardContent className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 p-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{video.title}</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      {formatDate(video.created_at)} / {formatDuration(video.duration_seconds)} /{' '}
                      {video.slide_count}枚 / {video.total_items}件
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-1 font-mono">{video.id}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 shrink-0">
                    {video.youtube_description && (
                      <CopyButton text={video.youtube_description} label="概要欄" />
                    )}
                    {video.chapters && <CopyButton text={video.chapters} label="章立て" />}
                    {video.thumbnail_path && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void runDownload(() => api.downloadThumbnail(video.id))}
                      >
                        <Image className="w-3.5 h-3.5" />
                        サムネ
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void runDownload(() => api.downloadVideo(video.id))}
                    >
                      <Download className="w-3.5 h-3.5" />
                      MP4
                    </Button>
                    {video.title_candidates && video.title_candidates.length > 0 && (
                      <CopyButton text={video.title_candidates.join('\n')} label="タイトル案" />
                    )}
                    {video.thumbnail_text_candidates &&
                      video.thumbnail_text_candidates.length > 0 && (
                        <CopyButton
                          text={video.thumbnail_text_candidates.join('\n\n---\n\n')}
                          label="サムネ案"
                        />
                      )}
                    {!video.youtube_description && (
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <FileText className="w-3.5 h-3.5" />
                        概要欄なし
                      </span>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </ScrollArea>
  )
}

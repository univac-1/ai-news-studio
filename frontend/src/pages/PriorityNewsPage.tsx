import { useState } from 'react'
import { ExternalLink, CheckCircle2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { usePriorityNews } from '@/hooks/usePriorityNews'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'

export function PriorityNewsPage() {
  const { news, loading, error, refetch } = usePriorityNews()
  const [marking, setMarking] = useState<string | null>(null)
  const [markedIds, setMarkedIds] = useState<Set<string>>(new Set())

  async function handleMarkUsed(id: string) {
    setMarking(id)
    try {
      await api.markAsUsed(id)
      setMarkedIds(prev => new Set([...prev, id]))
      refetch()
    } catch {
      // silently ignore — refetch will show current state
    } finally {
      setMarking(null)
    }
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-4 max-w-4xl mx-auto">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">優先度Aニュース</h2>
          {!loading && (
            <span className="text-sm text-muted-foreground">
              {news.length}件（使用済み除外）
            </span>
          )}
        </div>

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full rounded-lg" />
            ))}
          </div>
        ) : news.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
            <CheckCircle2 className="w-8 h-8 opacity-30" />
            <p className="text-sm">今週の未使用優先度Aニュースはありません</p>
          </div>
        ) : (
          <div className="space-y-3">
            {news.map(item => (
              <Card key={item.id} className={markedIds.has(item.id) ? 'opacity-50' : ''}>
                <CardContent className="flex items-start gap-3 p-4">
                  <Badge className="mt-0.5 shrink-0 bg-amber-100 text-amber-800 border-amber-200">
                    A
                  </Badge>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start gap-1.5">
                      <p className="text-sm font-medium line-clamp-2 flex-1">{item.title}</p>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="shrink-0 text-muted-foreground hover:text-foreground"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">
                      {item.source} · {formatDate(item.published_at)}
                      {item.model_name && item.model_name !== 'Unknown' && (
                        <> · <span className="text-blue-600">{item.model_name}</span></>
                      )}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className="shrink-0 text-xs"
                    disabled={marking === item.id || markedIds.has(item.id)}
                    onClick={() => handleMarkUsed(item.id)}
                  >
                    {marking === item.id
                      ? '記録中...'
                      : markedIds.has(item.id)
                      ? '記録済み'
                      : '使用済みに登録'}
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </ScrollArea>
  )
}

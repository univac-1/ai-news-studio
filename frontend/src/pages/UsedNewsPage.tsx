import { CheckCircle2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useUsedNews } from '@/hooks/useUsedNews'
import { useWeeklyNews } from '@/hooks/useWeeklyNews'
import { formatDate } from '@/lib/utils'

export function UsedNewsPage() {
  const { usedNews, loading: ul, error } = useUsedNews()
  const { news: weeklyNews, loading: wl } = useWeeklyNews()

  const loading = ul || wl
  const newsById = Object.fromEntries(weeklyNews.map(n => [n.id, n]))

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-4 max-w-4xl mx-auto">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">使用済みニュース</h2>
          {!loading && (
            <span className="text-sm text-muted-foreground">{usedNews.length}件</span>
          )}
        </div>

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full rounded-lg" />
            ))}
          </div>
        ) : usedNews.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
            <CheckCircle2 className="w-8 h-8 opacity-30" />
            <p className="text-sm">使用済みニュースはまだありません</p>
          </div>
        ) : (
          <div className="space-y-2">
            {usedNews.map(entry => {
              const item = newsById[entry.id]
              return (
                <Card key={entry.id}>
                  <CardContent className="flex items-start justify-between gap-3 p-4">
                    <div className="min-w-0 flex-1">
                      {item ? (
                        <>
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-sm font-medium hover:underline line-clamp-2"
                          >
                            {item.title}
                          </a>
                          <p className="text-xs text-muted-foreground mt-0.5">{item.source}</p>
                        </>
                      ) : (
                        <p className="text-xs font-mono text-muted-foreground break-all">{entry.id}</p>
                      )}
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="text-xs text-muted-foreground whitespace-nowrap">使用済み</p>
                      <p className="text-xs text-muted-foreground">{formatDate(entry.used_at)}</p>
                    </div>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        )}
      </div>
    </ScrollArea>
  )
}

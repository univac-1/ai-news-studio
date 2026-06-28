import { NewsItem } from '@/types/news'
import { NewsCard } from './NewsCard'
import { Skeleton } from '@/components/ui/skeleton'
import { AlertCircle, Newspaper } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'

interface NewsListProps {
  news: NewsItem[]
  loading: boolean
  error: string | null
  selectedId: string | null
  onSelect: (id: string) => void
}

export function NewsList({ news, loading, error, selectedId, onSelect }: NewsListProps) {
  const countA = news.filter(n => n.importance === 'A').length
  const countB = news.filter(n => n.importance === 'B').length

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b bg-white shrink-0">
        <div className="flex items-center gap-2 mb-1">
          <Newspaper className="w-4 h-4 text-primary" />
          <h1 className="font-semibold text-sm">直近7日のAIニュース</h1>
        </div>
        {!loading && !error && (
          <p className="text-xs text-muted-foreground">
            重要度A: <span className="font-medium text-amber-600">{countA}件</span>
            {' / '}B: {countB}件
          </p>
        )}
      </div>

      <ScrollArea className="flex-1">
        <div className="p-3 space-y-2">
          {loading && <LoadingSkeleton />}

          {error && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {!loading && !error && news.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-8">
              直近7日のニュースがありません
            </p>
          )}

          {!loading &&
            !error &&
            news.map(item => (
              <NewsCard
                key={item.id}
                item={item}
                selected={selectedId === item.id}
                onClick={() => onSelect(item.id)}
              />
            ))}
        </div>
      </ScrollArea>
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <>
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="p-3 rounded-lg border space-y-2">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      ))}
    </>
  )
}

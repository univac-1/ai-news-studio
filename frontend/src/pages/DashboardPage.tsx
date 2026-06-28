import { LucideIcon, CheckCircle, Newspaper, Star, Video } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useWeeklyNews } from '@/hooks/useWeeklyNews'
import { usePriorityNews } from '@/hooks/usePriorityNews'
import { useDraft } from '@/hooks/useDraft'
import { useUsedNews } from '@/hooks/useUsedNews'
import { formatDate } from '@/lib/utils'

function StatCard({
  title,
  value,
  icon: Icon,
  sub,
}: {
  title: string
  value: string | number
  icon: LucideIcon
  sub?: string
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-1 pt-4 px-4">
        <CardTitle className="text-xs font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="w-4 h-4 text-muted-foreground" />
      </CardHeader>
      <CardContent className="px-4 pb-4">
        <p className="text-2xl font-bold">{value}</p>
        {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  )
}

export function DashboardPage() {
  const { news: weeklyNews, loading: wl } = useWeeklyNews()
  const { news: priorityNews, loading: pl } = usePriorityNews()
  const { draft, loading: dl } = useDraft()
  const { usedNews, loading: ul } = useUsedNews()

  const loading = wl || pl || dl || ul

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-6 max-w-4xl mx-auto">
        <h2 className="text-lg font-semibold">ダッシュボード</h2>

        {loading ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="p-4 space-y-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-8 w-12" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              title="今週のニュース"
              value={weeklyNews.length}
              icon={Newspaper}
              sub="直近7日"
            />
            <StatCard
              title="優先度A（未使用）"
              value={priorityNews.length}
              icon={Star}
              sub="使用済み除外後"
            />
            <StatCard
              title="使用済み"
              value={usedNews.length}
              icon={CheckCircle}
              sub="累計"
            />
            <StatCard
              title="最新ドラフト"
              value={draft ? formatDate(draft.generated_at) : '未生成'}
              icon={Video}
              sub={draft ? `${draft.total_items}件` : ''}
            />
          </div>
        )}

        <div>
          <h3 className="text-sm font-semibold mb-3">優先度Aニュース（最新5件）</h3>
          {pl ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-lg" />
              ))}
            </div>
          ) : priorityNews.length === 0 ? (
            <p className="text-sm text-muted-foreground">今週の優先度Aニュースはありません</p>
          ) : (
            <div className="space-y-2">
              {priorityNews.slice(0, 5).map(item => (
                <a
                  key={item.id}
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-start gap-3 p-3 rounded-lg border bg-white hover:bg-gray-50 transition-colors"
                >
                  <Badge className="mt-0.5 shrink-0 bg-amber-100 text-amber-800 border-amber-200">
                    A
                  </Badge>
                  <div className="min-w-0">
                    <p className="text-sm font-medium line-clamp-2 text-foreground">{item.title}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {item.source} · {formatDate(item.published_at)}
                    </p>
                  </div>
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    </ScrollArea>
  )
}

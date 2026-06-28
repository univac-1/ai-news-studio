import { NewsItem } from '@/types/news'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { ExternalLink, TrendingUp, Zap, Tag } from 'lucide-react'
import { formatDate } from '@/lib/utils'

interface NewsDetailProps {
  item: NewsItem
}

export function NewsDetail({ item }: NewsDetailProps) {
  const isA = item.importance === 'A'

  return (
    <Card className="h-full rounded-none border-0 border-b shadow-none overflow-auto">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2 flex-wrap mb-2">
          <Badge
            className={
              isA
                ? 'bg-amber-500 text-white border-amber-500'
                : 'bg-secondary text-secondary-foreground'
            }
          >
            重要度 {item.importance}
          </Badge>
          <Badge variant="outline">{item.model_type}</Badge>
          {item.model_name !== 'Unknown' && (
            <Badge variant="outline" className="text-blue-700 border-blue-300 bg-blue-50">
              {item.model_name}
            </Badge>
          )}
          <span className="text-xs text-muted-foreground ml-auto">
            {item.source} · {formatDate(item.published_at)}
          </span>
        </div>

        <CardTitle className="text-base leading-snug">{item.title}</CardTitle>

        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-primary hover:underline mt-1"
        >
          元記事を読む <ExternalLink className="w-3 h-3" />
        </a>
      </CardHeader>

      {(item.image_generated_url || item.image_url) && (
        <div className="px-6 pb-4">
          <img
            src={item.image_generated_url ?? item.image_url ?? ''}
            alt={item.image_generated_alt ?? item.title}
            className="w-full h-40 object-cover rounded-lg"
            onError={e => {
              e.currentTarget.style.display = 'none'
            }}
          />
        </div>
      )}

      <CardContent className="space-y-4">
        <Section icon={<Zap className="w-4 h-4 text-blue-500" />} title="サマリー">
          {item.summary}
        </Section>

        <Separator />

        <Section icon={<TrendingUp className="w-4 h-4 text-emerald-500" />} title="インパクト">
          {item.impact}
        </Section>

        <Separator />

        <Section icon={<Zap className="w-4 h-4 text-violet-500" />} title="アクション">
          {item.action}
        </Section>

        {item.tags.length > 0 && (
          <>
            <Separator />
            <div className="flex items-start gap-2">
              <Tag className="w-4 h-4 text-muted-foreground mt-0.5 shrink-0" />
              <div className="flex flex-wrap gap-1">
                {item.tags.map(tag => (
                  <Badge key={tag} variant="secondary" className="text-xs">
                    {tag}
                  </Badge>
                ))}
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode
  title: string
  children: string
}) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1.5 text-sm font-medium">
        {icon}
        {title}
      </div>
      <p className="text-sm text-muted-foreground leading-relaxed">{children}</p>
    </div>
  )
}

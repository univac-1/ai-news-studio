import { NewsItem } from '@/types/news'
import { Badge } from '@/components/ui/badge'
import { cn, formatDate } from '@/lib/utils'

interface NewsCardProps {
  item: NewsItem
  selected: boolean
  onClick: () => void
}

export function NewsCard({ item, selected, onClick }: NewsCardProps) {
  const isA = item.importance === 'A'

  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full text-left px-3 py-3 rounded-lg border transition-colors',
        selected
          ? 'bg-primary/10 border-primary/40'
          : isA
          ? 'bg-amber-50 border-amber-200 hover:bg-amber-100'
          : 'bg-white border-border hover:bg-muted/50'
      )}
    >
      <div className="flex items-start gap-2 mb-1">
        <Badge
          className={cn(
            'shrink-0 text-[10px] px-1.5 py-0',
            isA
              ? 'bg-amber-500 text-white border-amber-500'
              : 'bg-secondary text-secondary-foreground border-secondary'
          )}
        >
          {item.importance}
        </Badge>
        <span className="text-xs text-muted-foreground leading-tight">
          {item.source} · {formatDate(item.published_at)}
        </span>
      </div>

      <p className="text-sm font-medium leading-snug line-clamp-2 mb-1.5">
        {item.title}
      </p>

      {item.model_name && item.model_name !== 'Unknown' && (
        <span className="inline-block text-[11px] bg-blue-100 text-blue-700 rounded px-1.5 py-0.5">
          {item.model_name}
        </span>
      )}
    </button>
  )
}

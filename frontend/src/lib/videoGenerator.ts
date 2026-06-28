import { NewsItem } from '@/types/news'
import { getWeekRangeLabel } from '@/lib/utils'

export interface VideoSegment {
  number: number
  headline: string
  summary: string
  impact: string
  action: string
  slideTitle: string
}

export interface VideoPlanDraft {
  title: string
  weekLabel: string
  intro: string
  segments: VideoSegment[]
  outro: string
  slideOutline: string[]
  totalItems: number
}

export function generateVideoPlan(items: NewsItem[]): VideoPlanDraft {
  const weekLabel = getWeekRangeLabel(items)

  const topProviders = [...new Set(items.slice(0, 3).map(i => i.provider))]
    .filter(p => p && p !== 'Unknown')
    .slice(0, 2)
    .join('、')

  const title = topProviders
    ? `今週のAIニュース速報（${weekLabel}）— ${topProviders} など注目アップデート`
    : `今週のAIニュース速報（${weekLabel}）— 重要AI動向まとめ`

  const intro =
    `今週は重要度Aのニュースが ${items.length} 件ありました。` +
    (topProviders ? `${topProviders} を中心に、` : '') +
    `AI業界の最新動向を一気にお届けします。`

  const segments: VideoSegment[] = items.map((item, i) => ({
    number: i + 1,
    headline: item.title,
    summary: item.summary,
    impact: item.impact,
    action: item.action,
    slideTitle: `#${i + 1} ${item.model_name !== 'Unknown' ? `[${item.model_name}] ` : ''}${item.title}`,
  }))

  const outro =
    '以上、今週のAI重要ニュースをお届けしました。' +
    '来週も最新情報をまとめてお届けしますので、チャンネル登録・通知オンをお忘れなく！'

  const slideOutline = [
    `[表紙] ${title}`,
    '[イントロ] 今週のハイライト',
    ...segments.map(s => s.slideTitle),
    '[アウトロ] チャンネル登録・来週の予告',
  ]

  return {
    title,
    weekLabel,
    intro,
    segments,
    outro,
    slideOutline,
    totalItems: items.length,
  }
}

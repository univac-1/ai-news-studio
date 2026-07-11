import { useState } from 'react'
import { NewsItem } from '@/types/news'
import { generateVideoPlan, VideoPlanDraft } from '@/lib/videoGenerator'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Video, ChevronDown, ChevronUp, Copy, Check, Layers } from 'lucide-react'

interface VideoPlanProps {
  priorityNews: NewsItem[]
}

export function VideoPlan({ priorityNews }: VideoPlanProps) {
  const [draft, setDraft] = useState<VideoPlanDraft | null>(null)
  const [expandedSegments, setExpandedSegments] = useState<Set<number>>(new Set())
  const [copied, setCopied] = useState(false)

  function handleGenerate() {
    setDraft(generateVideoPlan(priorityNews))
    setExpandedSegments(new Set())
  }

  function toggleSegment(n: number) {
    setExpandedSegments(prev => {
      const next = new Set(prev)
      next.has(n) ? next.delete(n) : next.add(n)
      return next
    })
  }

  function handleCopySlides() {
    if (!draft) return
    navigator.clipboard.writeText(draft.slideOutline.join('\n')).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  if (priorityNews.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-sm p-8 gap-2">
        <Video className="w-8 h-8 opacity-30" />
        <p>直近7日に重要度Aのニュースがありません</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b bg-white shrink-0 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Video className="w-4 h-4 text-primary" />
          <span className="font-semibold text-sm">動画企画ドラフト</span>
          <span className="text-xs text-muted-foreground">
            重要度Aニュース {priorityNews.length}件を自動ピックアップ
          </span>
        </div>
        <Button size="sm" onClick={handleGenerate}>
          {draft ? '再生成' : '企画を生成'}
        </Button>
      </div>

      {!draft ? (
        <div className="flex flex-col items-center justify-center flex-1 text-muted-foreground text-sm gap-2">
          <Video className="w-8 h-8 opacity-30" />
          <p>「企画を生成」を押すとドラフトが作成されます</p>
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="p-4 space-y-4">
            {/* Title */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-muted-foreground">動画タイトル案</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="font-semibold leading-snug">{draft.title}</p>
              </CardContent>
            </Card>

            {/* Intro */}
            <ScriptBlock label="イントロ（〜15秒）" content={draft.intro} />

            <Separator />

            {/* Segments */}
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                本編セグメント（各60〜90秒）
              </p>
              {draft.segments.map(seg => (
                <div key={seg.number} className="border rounded-lg overflow-hidden">
                  <button
                    className="w-full flex items-center justify-between px-3 py-2.5 text-left hover:bg-muted/50 transition-colors"
                    onClick={() => toggleSegment(seg.number)}
                  >
                    <span className="text-sm font-medium line-clamp-1">
                      #{seg.number} {seg.headline}
                    </span>
                    {expandedSegments.has(seg.number) ? (
                      <ChevronUp className="w-4 h-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronDown className="w-4 h-4 shrink-0 text-muted-foreground" />
                    )}
                  </button>

                  {expandedSegments.has(seg.number) && (
                    <div className="px-3 pb-3 space-y-2 border-t bg-muted/20">
                      <Field label="要約" value={seg.summary} />
                      <Field label="影響" value={seg.impact} />
                      <Field label="注目ポイント" value={seg.action} />
                    </div>
                  )}
                </div>
              ))}
            </div>

            <Separator />

            {/* Outro */}
            <ScriptBlock label="アウトロ（〜15秒）" content={draft.outro} />

            <Separator />

            {/* Slide outline */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  <Layers className="w-3.5 h-3.5" />
                  スライド構成
                </div>
                <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={handleCopySlides}>
                  {copied ? (
                    <>
                      <Check className="w-3 h-3 mr-1" /> コピー済み
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3 mr-1" /> コピー
                    </>
                  )}
                </Button>
              </div>
              <div className="rounded-lg bg-muted p-3 space-y-1">
                {draft.slideOutline.map((slide, i) => (
                  <p key={i} className="text-xs text-muted-foreground leading-relaxed">
                    {slide}
                  </p>
                ))}
              </div>
            </div>
          </div>
        </ScrollArea>
      )}
    </div>
  )
}

function ScriptBlock({ label, content }: { label: string; content: string }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-1.5">{label}</p>
      <p className="text-sm leading-relaxed bg-muted/30 rounded-lg px-3 py-2.5">{content}</p>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="pt-2">
      <p className="text-xs font-medium text-muted-foreground mb-0.5">{label}</p>
      <p className="text-sm leading-relaxed">{value}</p>
    </div>
  )
}

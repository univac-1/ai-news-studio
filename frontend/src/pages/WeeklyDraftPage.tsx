import { useState } from 'react'
import { Copy, Check, ChevronDown, ChevronUp, Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useDraft } from '@/hooks/useDraft'
import { VideoSegment } from '@/types/draft'
import { formatDate } from '@/lib/utils'

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  function handleCopy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <Button variant="ghost" size="sm" className="h-7 text-xs gap-1" onClick={handleCopy}>
      {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
      {copied ? 'コピー済み' : 'コピー'}
    </Button>
  )
}

function DraftSection({ title, children, copyText }: {
  title: string
  children: React.ReactNode
  copyText?: string
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2 pt-4 px-4">
        <CardTitle className="text-sm font-semibold">{title}</CardTitle>
        {copyText && <CopyButton text={copyText} />}
      </CardHeader>
      <CardContent className="px-4 pb-4">{children}</CardContent>
    </Card>
  )
}

function SegmentAccordion({ segment }: { segment: VideoSegment }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-3 py-2.5 text-left hover:bg-muted/50 transition-colors"
        onClick={() => setOpen(v => !v)}
      >
        <span className="text-sm font-medium line-clamp-1">#{segment.number} {segment.headline}</span>
        {open
          ? <ChevronUp className="w-4 h-4 shrink-0 text-muted-foreground" />
          : <ChevronDown className="w-4 h-4 shrink-0 text-muted-foreground" />
        }
      </button>
      {open && (
        <div className="px-3 pb-3 border-t bg-muted/20 space-y-2">
          {[
            ['要約', segment.summary],
            ['インパクト', segment.impact],
            ['アクション', segment.action],
          ].map(([label, value]) => (
            <div key={label} className="pt-2">
              <p className="text-xs font-medium text-muted-foreground mb-0.5">{label}</p>
              <p className="text-sm leading-relaxed">{value}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function WeeklyDraftPage() {
  const { draft, loading, generating, error, generate } = useDraft()

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-4 max-w-3xl mx-auto">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">週次ドラフト</h2>
            {draft && (
              <p className="text-xs text-muted-foreground">
                最終生成: {formatDate(draft.generated_at)} · {draft.total_items}件のニュースを使用
              </p>
            )}
          </div>
          <Button onClick={generate} disabled={generating} size="sm">
            {generating ? '生成中...' : draft ? '再生成' : '週次ドラフトを生成'}
          </Button>
        </div>

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-lg" />
            ))}
          </div>
        ) : !draft ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
            <Video className="w-8 h-8 opacity-30" />
            <p className="text-sm">「週次ドラフトを生成」ボタンを押してください</p>
          </div>
        ) : (
          <div className="space-y-4">
            <DraftSection title="タイトル候補" copyText={draft.title}>
              <p className="text-sm font-medium leading-snug">{draft.title}</p>
            </DraftSection>

            <DraftSection title="サムネイル文言" copyText={draft.thumbnail_text}>
              <pre className="text-sm leading-relaxed whitespace-pre-wrap font-sans">
                {draft.thumbnail_text}
              </pre>
            </DraftSection>

            <Card>
              <CardHeader className="pb-2 pt-4 px-4">
                <CardTitle className="text-sm font-semibold">台本</CardTitle>
              </CardHeader>
              <CardContent className="px-4 pb-4 space-y-3">
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1.5">イントロ（〜15秒）</p>
                  <p className="text-sm leading-relaxed bg-muted/30 rounded-lg px-3 py-2.5">
                    {draft.intro}
                  </p>
                </div>

                <Separator />

                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    本編セグメント（各60〜90秒）
                  </p>
                  {draft.segments.map(seg => (
                    <SegmentAccordion key={seg.number} segment={seg} />
                  ))}
                </div>

                <Separator />

                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1.5">アウトロ（〜15秒）</p>
                  <p className="text-sm leading-relaxed bg-muted/30 rounded-lg px-3 py-2.5">
                    {draft.outro}
                  </p>
                </div>
              </CardContent>
            </Card>

            <DraftSection title="スライド案" copyText={draft.slide_outline.join('\n')}>
              <div className="space-y-1">
                {draft.slide_outline.map((line, i) => (
                  <p key={i} className="text-xs text-muted-foreground leading-relaxed">{line}</p>
                ))}
              </div>
            </DraftSection>

            <DraftSection title="ナレーション原稿" copyText={draft.narration_script}>
              <pre className="text-xs leading-relaxed whitespace-pre-wrap font-sans text-muted-foreground max-h-48 overflow-auto">
                {draft.narration_script}
              </pre>
            </DraftSection>

            <DraftSection title="概要欄（YouTube Description）" copyText={draft.description}>
              <pre className="text-xs leading-relaxed whitespace-pre-wrap font-sans text-muted-foreground">
                {draft.description}
              </pre>
            </DraftSection>

            <DraftSection title="ハッシュタグ" copyText={draft.hashtags.join(' ')}>
              <div className="flex flex-wrap gap-1.5">
                {draft.hashtags.map(tag => (
                  <span
                    key={tag}
                    className="text-xs bg-blue-50 text-blue-700 border border-blue-200 rounded px-2 py-0.5"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </DraftSection>

            <DraftSection title="参照元URL" copyText={draft.reference_urls.join('\n')}>
              <div className="space-y-1">
                {draft.reference_urls.map((url, i) => (
                  <a
                    key={i}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-xs text-blue-600 hover:underline truncate"
                  >
                    {url}
                  </a>
                ))}
              </div>
            </DraftSection>
          </div>
        )}
      </div>
    </ScrollArea>
  )
}

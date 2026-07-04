import { useState } from 'react'
import type { ReactNode } from 'react'
import { Check, ChevronDown, ChevronUp, Copy, Download, Image, Loader2, Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useDraft } from '@/hooks/useDraft'
import { useVideos } from '@/hooks/useVideos'
import { VideoSegment } from '@/types/draft'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'

function CopyButton({ text, label = 'コピー' }: { text: string; label?: string }) {
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
      {copied ? 'コピー済み' : label}
    </Button>
  )
}

function DraftSection({
  title,
  children,
  copyText,
}: {
  title: string
  children: ReactNode
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
        <span className="text-sm font-medium line-clamp-1">
          #{segment.number} {segment.title_ja || segment.headline}
        </span>
        {open ? (
          <ChevronUp className="w-4 h-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="w-4 h-4 shrink-0 text-muted-foreground" />
        )}
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
  const {
    videos,
    loading: videosLoading,
    generating: videoGenerating,
    error: videoError,
    generate: generateVideo,
  } = useVideos()

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-4 max-w-3xl mx-auto">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">週次ドラフト</h2>
            {draft && (
              <p className="text-xs text-muted-foreground">
                最終生成: {formatDate(draft.generated_at)} / {draft.total_items}件のニュースを使用
              </p>
            )}
          </div>
          <Button onClick={generate} disabled={generating} size="sm" className="shrink-0">
            {generating ? '生成中...' : draft ? '再生成' : '週次ドラフトを生成'}
          </Button>
        </div>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 pt-4 px-4">
            <div>
              <CardTitle className="text-sm font-semibold">動画生成</CardTitle>
              <p className="text-xs text-muted-foreground mt-1">
                最新ドラフトから VOICEVOX 音声付きの 16:9 MP4 を生成します
              </p>
            </div>
            <Button
              onClick={generateVideo}
              disabled={!draft || videoGenerating}
              size="sm"
              className="shrink-0"
            >
              {videoGenerating && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {videoGenerating ? '生成中...' : '動画を生成'}
            </Button>
          </CardHeader>
          <CardContent className="px-4 pb-4 space-y-3">
            {videoError && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {videoError}
              </div>
            )}
            {videosLoading ? (
              <Skeleton className="h-14 w-full rounded-lg" />
            ) : videos.length === 0 ? (
              <p className="text-sm text-muted-foreground">生成済み動画はまだありません</p>
            ) : (
              <div className="space-y-2">
                {videos.slice(0, 5).map(video => (
                  <div
                    key={video.id}
                    className="flex items-center justify-between gap-3 rounded-lg border bg-white px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{video.title}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatDate(video.created_at)} / {Math.round(video.duration_seconds)}秒 / {video.slide_count}枚
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {video.youtube_description && (
                        <CopyButton text={video.youtube_description} label="概要欄" />
                      )}
                      {video.thumbnail_path && (
                        <Button variant="outline" size="sm" onClick={() => api.downloadThumbnail(video.id)}>
                          <Image className="w-3.5 h-3.5" />
                          サムネ
                        </Button>
                      )}
                      <Button variant="outline" size="sm" onClick={() => api.downloadVideo(video.id)}>
                        <Download className="w-3.5 h-3.5" />
                        MP4
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

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
            {(() => {
              const hasCandidates = draft.title_candidates && draft.title_candidates.length >= 2
              const copyContent = hasCandidates
                ? draft.title_candidates!.join('\n')
                : draft.title
              return (
                <DraftSection title="タイトル案" copyText={copyContent}>
                  {hasCandidates ? (
                    <ol className="text-sm font-medium leading-snug space-y-1">
                      {draft.title_candidates!.map((candidate, i) => (
                        <li key={i} className={i === 0 ? 'font-bold' : ''}>
                          {i + 1}. {candidate}
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="text-sm font-medium leading-snug">{draft.title}</p>
                  )}
                </DraftSection>
              )
            })()}

            {(() => {
              const candidates = draft.thumbnail_text_candidates ?? []
              const hasThumbCandidates = candidates.length >= 2
              return (
                <DraftSection
                  title="サムネイル文言案"
                  copyText={hasThumbCandidates ? candidates.join('\n\n---\n\n') : draft.thumbnail_text}
                >
                  {hasThumbCandidates ? (
                    <div className="space-y-2">
                      {candidates.map((candidate, i) => (
                        <pre
                          key={i}
                          className={`text-sm leading-relaxed whitespace-pre-wrap font-sans rounded-lg border px-3 py-2 ${
                            i === 0 ? 'font-bold bg-muted/30' : ''
                          }`}
                        >
                          {candidate}
                        </pre>
                      ))}
                    </div>
                  ) : (
                    <pre className="text-sm leading-relaxed whitespace-pre-wrap font-sans">
                      {draft.thumbnail_text}
                    </pre>
                  )}
                </DraftSection>
              )
            })()}

            <Card>
              <CardHeader className="pb-2 pt-4 px-4">
                <CardTitle className="text-sm font-semibold">台本</CardTitle>
              </CardHeader>
              <CardContent className="px-4 pb-4 space-y-3">
                {draft.hook && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1.5">フック（冒頭ティザー）</p>
                    <p className="text-sm leading-relaxed bg-muted/30 rounded-lg px-3 py-2.5">
                      {draft.hook}
                    </p>
                  </div>
                )}

                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1.5">イントロ</p>
                  <p className="text-sm leading-relaxed bg-muted/30 rounded-lg px-3 py-2.5">
                    {draft.intro}
                  </p>
                </div>

                <Separator />

                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    本編セグメント
                  </p>
                  {draft.segments.map(seg => (
                    <SegmentAccordion key={seg.number} segment={seg} />
                  ))}
                </div>

                <Separator />

                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1.5">アウトロ</p>
                  <p className="text-sm leading-relaxed bg-muted/30 rounded-lg px-3 py-2.5">
                    {draft.outro}
                  </p>
                </div>
              </CardContent>
            </Card>

            <DraftSection title="スライド案" copyText={draft.slide_outline.join('\n')}>
              <div className="space-y-1">
                {draft.slide_outline.map((line, i) => (
                  <p key={i} className="text-xs text-muted-foreground leading-relaxed">
                    {line}
                  </p>
                ))}
              </div>
            </DraftSection>

            <DraftSection title="ナレーション原稿" copyText={draft.narration_script}>
              <pre className="text-xs leading-relaxed whitespace-pre-wrap font-sans text-muted-foreground max-h-48 overflow-auto">
                {draft.narration_script}
              </pre>
            </DraftSection>

            <DraftSection title="YouTube 概要欄" copyText={draft.description}>
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

            <DraftSection title="参考 URL" copyText={draft.reference_urls.join('\n')}>
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

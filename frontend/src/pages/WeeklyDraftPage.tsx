import { useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronDown, ChevronUp, Loader2, PlayCircle, Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { CopyButton } from '@/components/CopyButton'
import { useDraft } from '@/hooks/useDraft'
import { useVideos } from '@/hooks/useVideos'
import { VideoArtifact, VideoSegment } from '@/types/draft'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'

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

function GeneratedVideoNotice({ video }: { video: VideoArtifact }) {
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
      動画を生成しました: {video.title} / {Math.round(video.duration_seconds)}秒 /{' '}
      {video.slide_count}枚
    </div>
  )
}

export function WeeklyDraftPage() {
  const { draft, loading, generating, error, generate, replaceDraft } = useDraft()
  const {
    generating: videoGenerating,
    error: videoError,
    generate: generateVideo,
  } = useVideos()
  const [bulkGenerating, setBulkGenerating] = useState(false)
  const [bulkStatus, setBulkStatus] = useState<string | null>(null)
  const [bulkError, setBulkError] = useState<string | null>(null)
  const [lastVideo, setLastVideo] = useState<VideoArtifact | null>(null)

  const busy = generating || videoGenerating || bulkGenerating

  async function handleGenerateDraftOnly() {
    const nextDraft = await generate()
    if (nextDraft) {
      setBulkError(null)
      setLastVideo(null)
    }
  }

  async function handleGenerateVideoOnly() {
    const video = await generateVideo()
    if (video) {
      setBulkError(null)
      setLastVideo(video)
    }
  }

  async function handleBulkGenerate() {
    setBulkGenerating(true)
    setBulkStatus('台本生成と動画生成を実行中です。完了まで数分かかることがあります。')
    setBulkError(null)
    setLastVideo(null)
    try {
      const result = await api.generateWeeklyDraftAndVideo()
      replaceDraft(result.draft)
      setLastVideo(result.video)
      setBulkStatus('完了しました。生成済み動画は「動画一覧」から整理できます。')
    } catch (e) {
      setBulkStatus(null)
      setBulkError(e instanceof Error ? e.message : '一括生成に失敗しました')
    } finally {
      setBulkGenerating(false)
    }
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-4 max-w-3xl mx-auto">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">週次ドラフト</h2>
            <p className="text-xs text-muted-foreground mt-1">
              台本生成から動画生成までの制作操作をまとめて実行します
            </p>
            {draft && (
              <p className="text-xs text-muted-foreground mt-1">
                最終生成: {formatDate(draft.generated_at)} / {draft.total_items}件のニュースを使用
              </p>
            )}
          </div>
        </div>

        <Card>
          <CardHeader className="pb-2 pt-4 px-4">
            <CardTitle className="text-sm font-semibold">制作</CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 space-y-3">
            <div className="flex flex-col sm:flex-row gap-2">
              <Button onClick={handleBulkGenerate} disabled={busy} className="sm:flex-1">
                {bulkGenerating ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <PlayCircle className="w-4 h-4" />
                )}
                {bulkGenerating ? '一括生成中...' : '台本と動画を一括生成'}
              </Button>
              <Button
                variant="outline"
                onClick={handleGenerateDraftOnly}
                disabled={busy}
                className="sm:w-36"
              >
                {generating && <Loader2 className="w-4 h-4 animate-spin" />}
                台本のみ
              </Button>
              <Button
                variant="outline"
                onClick={handleGenerateVideoOnly}
                disabled={!draft || busy}
                className="sm:w-44"
              >
                {videoGenerating && <Loader2 className="w-4 h-4 animate-spin" />}
                最新台本から動画
              </Button>
            </div>

            {bulkStatus && (
              <div className="rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                {bulkStatus}
              </div>
            )}
            {lastVideo && <GeneratedVideoNotice video={lastVideo} />}
            {(error || videoError || bulkError) && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {bulkError || videoError || error}
              </div>
            )}
          </CardContent>
        </Card>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-lg" />
            ))}
          </div>
        ) : !draft ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
            <Video className="w-8 h-8 opacity-30" />
            <p className="text-sm">「台本と動画を一括生成」または「台本のみ」を実行してください</p>
          </div>
        ) : (
          <div className="space-y-4">
            {(() => {
              const hasCandidates = draft.title_candidates && draft.title_candidates.length >= 2
              const copyContent = hasCandidates ? draft.title_candidates!.join('\n') : draft.title
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
                    <p className="text-xs font-medium text-muted-foreground mb-1.5">
                      フック（冒頭ティザー）
                    </p>
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

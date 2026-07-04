export interface SegmentVisual {
  type: 'flow' | 'command'
  items: string[]
}

export interface VideoSegment {
  number: number
  headline: string
  summary: string
  impact: string
  action: string
  slide_title: string
  narration: string
  title_ja?: string
  category?: string
  visual?: SegmentVisual | null
  rank_reason?: string
}

export interface VideoPlanDraft {
  title: string
  week_label: string
  thumbnail_text: string
  thumbnail_text_candidates?: string[]
  intro: string
  segments: VideoSegment[]
  outro: string
  slide_outline: string[]
  narration_script: string
  description: string
  hashtags: string[]
  reference_urls: string[]
  total_items: number
  generated_at: string
  hook?: string
  title_candidates?: string[]
}

export interface UsedNewsEntry {
  id: string
  used_at: string
}

export interface VideoArtifact {
  id: string
  title: string
  created_at: string
  draft_generated_at: string
  total_items: number
  duration_seconds: number
  video_path: string
  subtitles_path: string
  slide_count: number
  chapters?: string
  youtube_description?: string
  thumbnail_path?: string
  title_candidates?: string[]
  thumbnail_text_candidates?: string[]
}

export interface VideoArtifactList {
  items: VideoArtifact[]
}

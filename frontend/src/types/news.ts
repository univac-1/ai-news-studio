export interface NewsItem {
  id: string
  title: string
  url: string
  source: string
  published_at: string
  collected_at: string
  image_url: string | null
  is_model_related: boolean
  model_relevance_reason: string
  model_name: string
  provider: string
  model_type: string
  summary: string
  impact: string
  action: string
  importance: 'A' | 'B'
  tags: string[]
  language: string
  image_generated_url: string | null
  image_generated_alt: string | null
  image_generated_prompt: string | null
}

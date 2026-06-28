import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function isWithinLastNDays(dateStr: string, days: number): boolean {
  const date = new Date(dateStr)
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - days)
  return date >= cutoff
}

export function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('ja-JP', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

export function getWeekRangeLabel(items: { published_at: string }[]): string {
  if (items.length === 0) return ''
  const dates = items.map(i => new Date(i.published_at))
  const min = new Date(Math.min(...dates.map(d => d.getTime())))
  const max = new Date(Math.max(...dates.map(d => d.getTime())))
  const fmt = (d: Date) =>
    d.toLocaleDateString('ja-JP', { month: 'long', day: 'numeric' })
  return `${fmt(min)}〜${fmt(max)}`
}

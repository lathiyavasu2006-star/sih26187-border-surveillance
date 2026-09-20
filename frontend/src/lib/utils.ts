import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import type { RiskLevel } from '@/types'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export const IST_TIMEZONE = 'Asia/Kolkata'

const dateTimeFormat = new Intl.DateTimeFormat('en-IN', {
  timeZone: IST_TIMEZONE,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})
const timeFormat = new Intl.DateTimeFormat('en-IN', {
  timeZone: IST_TIMEZONE,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})
const dateFormat = new Intl.DateTimeFormat('en-IN', {
  timeZone: IST_TIMEZONE,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

function toDate(value: string | number | Date): Date | null {
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDateTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const date = toDate(value)
  return date ? `${dateTimeFormat.format(date)} IST` : '—'
}

export function formatTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const date = toDate(value)
  return date ? timeFormat.format(date) : '—'
}

export function formatDate(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const date = toDate(value)
  return date ? dateFormat.format(date) : '—'
}

export function formatRelative(value: string | number | Date | null | undefined, now: number = Date.now()): string {
  if (value === null || value === undefined) return '—'
  const date = toDate(value)
  if (!date) return '—'
  const seconds = Math.round((now - date.getTime()) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds === null || totalSeconds === undefined || !Number.isFinite(totalSeconds)) return '—'
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return '—'
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(' ')
}

/** Mirrors backend settings.get_risk_level: 0-20 normal, 21-40 low, 41-60 suspicious, 61-80 high, 81-100 critical. */
export function riskLevelForScore(score: number): RiskLevel {
  if (score <= 20) return 'normal'
  if (score <= 40) return 'low'
  if (score <= 60) return 'suspicious'
  if (score <= 80) return 'high_risk'
  return 'critical'
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function shortHash(hash: string | null | undefined, size = 10): string {
  if (!hash) return '—'
  return hash.length <= size * 2 ? hash : `${hash.slice(0, size)}…${hash.slice(-size)}`
}

/** Point-in-polygon (ray casting) in pixel space. */
export function pointInPolygon(x: number, y: number, polygon: readonly (readonly [number, number])[]): boolean {
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const pi = polygon[i]
    const pj = polygon[j]
    if (!pi || !pj) continue
    const [xi, yi] = pi
    const [xj, yj] = pj
    const intersects = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi
    if (intersects) inside = !inside
  }
  return inside
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 00:00 IST of the day containing `at`, as an ISO string (the backend's reporting-day boundary). */
export function istDayStartIso(at: number = Date.now()): string {
  const istOffsetMs = 330 * 60_000
  const istNow = new Date(at + istOffsetMs)
  const midnightIst = Date.UTC(istNow.getUTCFullYear(), istNow.getUTCMonth(), istNow.getUTCDate()) - istOffsetMs
  return new Date(midnightIst).toISOString()
}

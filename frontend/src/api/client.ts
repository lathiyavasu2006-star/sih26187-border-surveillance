import axios, { AxiosError, AxiosHeaders, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import { useAuthStore } from '@/stores/authStore'
import type { AccessTokenResponse } from '@/types'

export const API_BASE_URL: string = import.meta.env.VITE_API_URL || '/api'

/** Error surfaced to the UI. `detail` is FastAPI's error message (string or first validation message). */
export class ApiError extends Error {
  readonly status: number
  readonly retryAfterSeconds: number | null

  constructor(message: string, status: number, retryAfterSeconds: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfterSeconds = retryAfterSeconds
  }
}

interface ValidationItem {
  msg?: unknown
  loc?: unknown
}

function detailMessage(data: unknown, fallback: string): string {
  if (typeof data === 'object' && data !== null && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as ValidationItem
      const location = Array.isArray(first.loc) ? first.loc.filter((part) => part !== 'body').join('.') : ''
      const msg = typeof first.msg === 'string' ? first.msg : fallback
      return location ? `${location}: ${msg}` : msg
    }
  }
  if (typeof data === 'object' && data !== null && 'error' in data) {
    const error = (data as { error: unknown }).error
    if (typeof error === 'string') return error
  }
  return fallback
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (axios.isAxiosError(error)) {
    const response = error.response
    if (!response) {
      return new ApiError('Backend unreachable — check that the API server is running', 0)
    }
    const retryHeader = response.headers['retry-after']
    const retryAfter = typeof retryHeader === 'string' ? Number.parseInt(retryHeader, 10) : Number.NaN
    const fallback = response.status === 429 ? 'Too many requests' : `Request failed (${response.status})`
    return new ApiError(
      detailMessage(response.data, fallback),
      response.status,
      Number.isFinite(retryAfter) ? retryAfter : null,
    )
  }
  if (error instanceof Error) return new ApiError(error.message, 0)
  return new ApiError('Unexpected error', 0)
}

export const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30_000,
  headers: { Accept: 'application/json' },
})

// A bare client for the refresh call so its own 401 never recurses into the refresh interceptor.
const refreshClient = axios.create({ baseURL: API_BASE_URL, timeout: 15_000 })

let refreshInFlight: Promise<string | null> | null = null

/** Exchanges the refresh token for a new access token. Concurrent callers share one request. */
export function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight
  const { refreshToken } = useAuthStore.getState()
  if (!refreshToken) return Promise.resolve(null)
  refreshInFlight = refreshClient
    .post<AccessTokenResponse>('/auth/refresh', { refresh_token: refreshToken })
    .then((response) => {
      useAuthStore.getState().setAccessToken(response.data.access_token, response.data.expires_in)
      return response.data.access_token
    })
    .catch((error: unknown) => {
      // Only a rejected refresh token ends the session. A network error or 5xx (backend restarting) keeps
      // the operator signed in so the next attempt can succeed once the server is back.
      const status = axios.isAxiosError(error) ? error.response?.status : undefined
      if (status === 401 || status === 403 || status === 422) {
        useAuthStore.getState().clearSession('Session expired — please sign in again')
      }
      return null
    })
    .finally(() => {
      refreshInFlight = null
    })
  return refreshInFlight
}

interface RetriableConfig extends InternalAxiosRequestConfig {
  _retried?: boolean
}

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
  if (token) {
    const headers = AxiosHeaders.from(config.headers)
    headers.set('Authorization', `Bearer ${token}`)
    config.headers = headers
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as RetriableConfig | undefined
    const isAuthCall = original?.url?.startsWith('/auth/login') || original?.url?.startsWith('/auth/refresh')
    if (error.response?.status === 401 && original && !original._retried && !isAuthCall) {
      original._retried = true
      const token = await refreshAccessToken()
      if (token) {
        const headers = AxiosHeaders.from(original.headers)
        headers.set('Authorization', `Bearer ${token}`)
        original.headers = headers
        return api.request(original)
      }
    }
    return Promise.reject(toApiError(error))
  },
)

/** Normalizes a backend file URL ("/evidence/files/...") to a path for the authenticated api client. */
export function evidenceRequestPath(fileUrl: string | null | undefined): string | null {
  if (!fileUrl || !fileUrl.startsWith('/evidence/files/')) return null
  return fileUrl
}

const EVIDENCE_ROOT = (import.meta.env.VITE_EVIDENCE_ROOT || '').replaceAll('\\', '/').replace(/\/+$/, '')

/**
 * Alerts store the absolute snapshot path on the backend host. The backend serves the same file at
 * /evidence/files/<path relative to the evidence root>. Returns null for paths outside the root.
 */
export function snapshotRequestPath(snapshotPath: string | null | undefined): string | null {
  if (!snapshotPath || !EVIDENCE_ROOT) return null
  const normalized = snapshotPath.replaceAll('\\', '/')
  if (!normalized.toLowerCase().startsWith(`${EVIDENCE_ROOT.toLowerCase()}/`)) return null
  const parts = normalized.slice(EVIDENCE_ROOT.length + 1).split('/')
  if (parts.some((part) => part === '..' || part === '')) return null
  return `/evidence/files/${parts.map(encodeURIComponent).join('/')}`
}

const blobCache = new Map<string, Promise<string>>()

/**
 * Downloads a protected file with the Authorization header and returns an object URL. The JWT is never
 * placed in a URL (no leakage into history, proxy logs or Referer). Results are cached per path so a
 * gallery does not spend the per-client rate limit re-downloading the same file.
 */
export function fetchProtectedObjectUrl(requestPath: string): Promise<string> {
  const cached = blobCache.get(requestPath)
  if (cached) return cached
  const pending = api
    .get<Blob>(requestPath, { responseType: 'blob', timeout: 120_000 })
    .then((response) => URL.createObjectURL(response.data))
  pending.catch(() => blobCache.delete(requestPath))
  blobCache.set(requestPath, pending)
  return pending
}

export function clearProtectedObjectUrls(): void {
  for (const pending of blobCache.values()) {
    pending.then((url) => URL.revokeObjectURL(url)).catch(() => undefined)
  }
  blobCache.clear()
}

/** Removes undefined / empty-string values so they are not sent as query parameters. */
export function cleanParams<T extends object>(params: T): Partial<T> {
  const result: Partial<T> = {}
  for (const [key, value] of Object.entries(params) as [keyof T, T[keyof T]][]) {
    if (value !== undefined && value !== null && value !== '') result[key] = value
  }
  return result
}

import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '@/api/client'

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        // Never hammer the API: retry network/5xx once, never retry 4xx (auth, validation, rate limit).
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
          return failureCount < 1
        },
        retryDelay: (attempt, error) =>
          error instanceof ApiError && error.retryAfterSeconds ? error.retryAfterSeconds * 1000 : Math.min(10_000, 2_000 * 2 ** attempt),
      },
      mutations: { retry: false },
    },
  })
}

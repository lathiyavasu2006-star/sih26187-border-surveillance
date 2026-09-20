import { AxiosError, AxiosHeaders } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { refreshAccessToken } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { makeToken } from '@/test/fixtures'

/**
 * Regression: stopping the backend used to sign the operator out, because a failed refresh request was
 * treated as an expired session. Only a refresh the server rejects may end the session.
 */
const httpError = (status: number) =>
  new AxiosError('failed', 'ERR', undefined, undefined, {
    status,
    statusText: '',
    data: { detail: 'x' },
    headers: {},
    config: { headers: new AxiosHeaders() },
  })

async function refreshWith(adapterError: Error): Promise<string | null> {
  const axios = await import('axios')
  const spy = vi.spyOn(axios.default.Axios.prototype, 'request').mockRejectedValue(adapterError)
  try {
    return await refreshAccessToken()
  } finally {
    spy.mockRestore()
  }
}

describe('refreshAccessToken', () => {
  beforeEach(() => useAuthStore.getState().setSession(makeToken()))
  afterEach(() => useAuthStore.getState().clearSession())

  it('keeps the session when the backend is unreachable', async () => {
    expect(await refreshWith(new AxiosError('Network Error', 'ERR_NETWORK'))).toBeNull()
    expect(useAuthStore.getState().refreshToken).toBe('refresh-token-value')
    expect(useAuthStore.getState().user?.username).toBe('admin')
  })

  it('keeps the session on a 502 from the dev proxy', async () => {
    expect(await refreshWith(httpError(502))).toBeNull()
    expect(useAuthStore.getState().accessToken).toBe('access-token-value')
  })

  it('ends the session when the server rejects the refresh token', async () => {
    expect(await refreshWith(httpError(401))).toBeNull()
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().sessionMessage).toMatch(/Session expired/)
  })
})

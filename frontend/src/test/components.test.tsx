import { QueryClientProvider } from '@tanstack/react-query'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { authApi, statsApi } from '@/api/endpoints'
import { AlertPanel } from '@/components/alerts/AlertPanel'
import { ShortcutsOverlay } from '@/components/modals/ShortcutsOverlay'
import { validateCameraDraft } from '@/lib/validation'
import { createQueryClient } from '@/lib/queryClient'
import { LoginPage } from '@/pages/LoginPage'
import { useAuthStore } from '@/stores/authStore'
import { makeAlert, makeToken } from '@/test/fixtures'

function wrap(children: ReactNode, path = '/login') {
  return (
    <QueryClientProvider client={createQueryClient()}>
      <TooltipPrimitive.Provider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/login" element={children} />
            <Route path="/dashboard" element={<p>dashboard route</p>} />
          </Routes>
        </MemoryRouter>
      </TooltipPrimitive.Provider>
    </QueryClientProvider>
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    useAuthStore.getState().clearSession()
    vi.spyOn(statsApi, 'health').mockResolvedValue({ status: 'healthy', db: 'connected', camera_monitor: 'running', timestamp: '' })
  })
  afterEach(() => vi.restoreAllMocks())

  it('signs in with the OAuth2 form and routes to the dashboard', async () => {
    const login = vi.spyOn(authApi, 'login').mockResolvedValue(makeToken())
    render(wrap(<LoginPage />))
    expect(await screen.findByText(/API healthy/)).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('login-username'), { target: { value: ' admin ' } })
    fireEvent.change(screen.getByTestId('login-password'), { target: { value: 'Correct#Horse2026' } })
    fireEvent.click(screen.getByTestId('login-submit'))
    expect(await screen.findByText('dashboard route')).toBeInTheDocument()
    expect(login).toHaveBeenCalledWith('admin', 'Correct#Horse2026')
    expect(useAuthStore.getState().user?.username).toBe('admin')
  })

  it('shows the backend error, clears the password and stays signed out', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new ApiError('Incorrect username or password', 401))
    render(wrap(<LoginPage />))
    fireEvent.change(screen.getByTestId('login-username'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByTestId('login-password'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByTestId('login-submit'))
    expect(await screen.findByTestId('login-error')).toHaveTextContent('Incorrect username or password')
    expect(screen.getByTestId('login-password')).toHaveValue('')
    expect(useAuthStore.getState().accessToken).toBeNull()
  })

  it('surfaces rate limiting with the Retry-After delay', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new ApiError('Too many requests', 429, 37))
    render(wrap(<LoginPage />))
    fireEvent.change(screen.getByTestId('login-username'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByTestId('login-password'), { target: { value: 'x' } })
    fireEvent.click(screen.getByTestId('login-submit'))
    await waitFor(() => expect(screen.getByTestId('login-error')).toHaveTextContent('37 seconds'))
  })

  it('does not call the API with empty credentials', async () => {
    const login = vi.spyOn(authApi, 'login')
    render(wrap(<LoginPage />))
    fireEvent.click(screen.getByTestId('login-submit'))
    expect(await screen.findByTestId('login-error')).toHaveTextContent('Enter your username and password')
    expect(login).not.toHaveBeenCalled()
  })
})

describe('ShortcutsOverlay', () => {
  it('lists every shortcut group in the dark 4-column overlay', () => {
    render(wrap(<ShortcutsOverlay open onOpenChange={() => undefined} />))
    const overlay = screen.getByTestId('shortcuts-overlay')
    for (const group of ['Navigation', 'Camera', 'Map', 'Security', 'Alerts']) expect(overlay).toHaveTextContent(group)
    expect(overlay).toHaveTextContent('Lock screen')
    expect(overlay).toHaveTextContent('Panic button')
    expect(overlay).toHaveTextContent('Cycle map styles')
    expect(overlay).toHaveTextContent('Mute / unmute alarm')
    expect(overlay).toHaveTextContent('Auto-lock after inactivity')
  })
})

describe('AlertPanel', () => {
  it('renders alerts with risk, camera and track and reports selection', () => {
    const onSelect = vi.fn()
    const alert = makeAlert()
    render(wrap(<AlertPanel alerts={[alert]} selectedId={null} onSelect={onSelect} now={Date.parse(alert.timestamp) + 90_000} />))
    const row = screen.getByTestId(`alert-row-${alert.alert_id}`)
    expect(row).toHaveTextContent('Loitering')
    expect(row).toHaveTextContent('CAM-N-001 · ID 1 · Gate')
    expect(row).toHaveTextContent('High Risk80')
    expect(row).toHaveTextContent('1m ago')
    fireEvent.click(row)
    expect(onSelect).toHaveBeenCalledWith(alert)
  })

  it('shows an empty state', () => {
    render(wrap(<AlertPanel alerts={[]} selectedId={null} onSelect={() => undefined} now={0} emptyTitle="Nothing open" />))
    expect(screen.getByText('Nothing open')).toBeInTheDocument()
  })
})

describe('camera registration rules', () => {
  it('require a stream on the stream step', () => {
    expect(
      validateCameraDraft(
        { name: 'x', camera_type: 'standard', zone_region: 'north', sector_name: '', location_name: '', gps_lat: '', gps_lng: '', rtsp_url: '', device_id: '' },
        2,
      ),
    ).toHaveProperty('rtsp_url')
  })
})

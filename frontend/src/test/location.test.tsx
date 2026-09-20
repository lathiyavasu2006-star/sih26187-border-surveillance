import { QueryClientProvider } from '@tanstack/react-query'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { camerasApi, zonesApi } from '@/api/endpoints'
import { CameraEditDialog } from '@/components/camera/CameraEditDialog'
import { LocateCameraDialog } from '@/components/camera/LocateCameraDialog'
import { FocusedCameraPanel } from '@/components/map/FocusedCameraPanel'
import { useLockKeyReleaseFallback } from '@/hooks/useSession'
import { describeAccuracy, locateConsole } from '@/lib/geolocation'
import { GEOFENCE_RINGS } from '@/lib/mapData'
import { createQueryClient } from '@/lib/queryClient'
import { useAuthStore } from '@/stores/authStore'
import { makeToken } from '@/test/fixtures'
import type { CameraWithAlertCount, HostLocation, Zone } from '@/types'

function makeCamera(overrides: Partial<CameraWithAlertCount> = {}): CameraWithAlertCount {
  return {
    camera_id: 'CAM-N-001',
    name: 'Webcam',
    location_name: null,
    gps_lat: null,
    gps_lng: null,
    rtsp_url: null,
    device_id: '0',
    camera_type: 'standard',
    zone_region: 'north',
    sector_name: null,
    status: 'online',
    registered_at: '2026-09-17T00:00:00Z',
    last_seen: '2026-09-18T00:00:00Z',
    active_alert_count: 0,
    calibrated: false,
    calibration_error_px: null,
    ...overrides,
  }
}

const HOST: HostLocation = { lat: 21.216295, lng: 72.86377, accuracy_m: 212, source: 'windows-location-service', captured_at: '2026-09-19T08:00:00Z' }

function LocationProbe() {
  const location = useLocation()
  return <p data-testid="route">{location.pathname + location.search}</p>
}

function wrap(children: ReactNode) {
  return (
    <QueryClientProvider client={createQueryClient()}>
      <TooltipPrimitive.Provider>
        <MemoryRouter initialEntries={['/cameras']}>
          <Routes>
            <Route path="/cameras" element={children} />
            <Route path="/map" element={<LocationProbe />} />
            <Route path="/zones" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </TooltipPrimitive.Provider>
    </QueryClientProvider>
  )
}

/** Browser geolocation with a given permission state; `position` null makes getCurrentPosition fail as denied. */
function stubBrowser(permission: PermissionState, position: { lat: number; lng: number; accuracy: number } | null) {
  const getCurrentPosition = vi.fn((success: PositionCallback, failure?: PositionErrorCallback | null) => {
    if (position) success({ coords: { latitude: position.lat, longitude: position.lng, accuracy: position.accuracy } } as GeolocationPosition)
    else failure?.({ code: 1, PERMISSION_DENIED: 1, TIMEOUT: 3, POSITION_UNAVAILABLE: 2, message: 'denied' } as GeolocationPositionError)
  })
  vi.stubGlobal('navigator', {
    ...navigator,
    geolocation: { getCurrentPosition },
    permissions: { query: vi.fn().mockResolvedValue({ state: permission }) },
  })
  return getCurrentPosition
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('locateConsole', () => {
  it('uses the browser position when location is allowed', async () => {
    stubBrowser('granted', { lat: 21.1702401, lng: 72.8310607, accuracy: 18 })
    const host = vi.spyOn(camerasApi, 'hostLocation')
    await expect(locateConsole()).resolves.toEqual({ lat: 21.17024, lng: 72.831061, accuracy: 18, source: 'browser' })
    expect(host).not.toHaveBeenCalled()
  })

  it('falls back to the Windows location service when the browser blocks location', async () => {
    const getCurrentPosition = stubBrowser('denied', null)
    vi.spyOn(camerasApi, 'hostLocation').mockResolvedValue(HOST)
    const steps: string[] = []
    await expect(locateConsole({ onStep: (step) => steps.push(step) })).resolves.toEqual({ lat: 21.216295, lng: 72.86377, accuracy: 212, source: 'windows' })
    expect(getCurrentPosition).not.toHaveBeenCalled() // a blocked permission is never re-prompted
    expect(steps).toEqual(['windows'])
  })

  it('falls back when the browser prompt is refused', async () => {
    stubBrowser('prompt', null)
    vi.spyOn(camerasApi, 'hostLocation').mockResolvedValue(HOST)
    await expect(locateConsole()).resolves.toMatchObject({ source: 'windows' })
  })

  it('does not raise a browser prompt when asked not to (page-load auto-locate)', async () => {
    const getCurrentPosition = stubBrowser('prompt', { lat: 1, lng: 1, accuracy: 1 })
    vi.spyOn(camerasApi, 'hostLocation').mockResolvedValue(HOST)
    await expect(locateConsole({ promptBrowser: false })).resolves.toMatchObject({ source: 'windows' })
    expect(getCurrentPosition).not.toHaveBeenCalled()
  })

  it('explains both failures', async () => {
    stubBrowser('denied', null)
    vi.spyOn(camerasApi, 'hostLocation').mockRejectedValue(new ApiError('Location is turned off for this computer', 503))
    await expect(locateConsole()).rejects.toThrow('Location permission is blocked in this browser. Location is turned off for this computer.')
  })

  it('describes an unreported accuracy', () => {
    expect(describeAccuracy(null)).toBe('accuracy not reported')
  })
})

describe('LocateCameraDialog', () => {
  it('locates, saves the GPS and opens the camera on the map', async () => {
    stubBrowser('denied', null)
    vi.spyOn(camerasApi, 'hostLocation').mockResolvedValue(HOST)
    const edit = vi.spyOn(camerasApi, 'edit').mockResolvedValue(makeCamera({ gps_lat: HOST.lat, gps_lng: HOST.lng }))
    const onClose = vi.fn()
    render(wrap(<LocateCameraDialog camera={makeCamera()} onClose={onClose} />))
    expect(await screen.findByText(/Blocked in this browser/)).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('enable-location'))
    await waitFor(() => expect(screen.getByTestId('route')).toHaveTextContent('/map?focus=CAM-N-001'))
    expect(edit).toHaveBeenCalledWith('CAM-N-001', {
      gps_lat: 21.216295,
      gps_lng: 72.86377,
      location_name: 'Console position (±212 m, Windows location service)',
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('shows why location failed and stays open to retry', async () => {
    stubBrowser('denied', null)
    vi.spyOn(camerasApi, 'hostLocation').mockRejectedValue(new ApiError('Location is turned off for this computer', 503))
    const edit = vi.spyOn(camerasApi, 'edit')
    render(wrap(<LocateCameraDialog camera={makeCamera()} onClose={() => undefined} />))
    fireEvent.click(screen.getByTestId('enable-location'))
    expect(await screen.findByTestId('locate-error')).toHaveTextContent('Location is turned off for this computer')
    expect(screen.getByTestId('enable-location')).toHaveTextContent('Try again')
    expect(edit).not.toHaveBeenCalled()
  })

  it('warns that a network camera is only here if installed here', () => {
    stubBrowser('granted', null)
    render(wrap(<LocateCameraDialog camera={makeCamera({ device_id: null, rtsp_url: 'rtsp://10.0.0.2/s' })} onClose={() => undefined} />))
    expect(screen.getByText(/This is a network camera/)).toBeInTheDocument()
  })
})

describe('CameraEditDialog → Use current location', () => {
  it('opens the location dialog and saves other unsaved edits together with the position', async () => {
    stubBrowser('granted', { lat: 32.7266, lng: 74.857, accuracy: 20 })
    const edit = vi.spyOn(camerasApi, 'edit').mockResolvedValue(makeCamera({ gps_lat: 32.7266, gps_lng: 74.857 }))
    const onClose = vi.fn()
    render(wrap(<CameraEditDialog camera={makeCamera({ location_name: 'Home post' })} onClose={onClose} />))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Gate camera' } })
    fireEvent.click(screen.getByTestId('use-current-location'))
    expect(await screen.findByTestId('locate-camera-dialog')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('enable-location'))
    await waitFor(() => expect(screen.getByTestId('route')).toHaveTextContent('/map?focus=CAM-N-001'))
    expect(edit).toHaveBeenCalledWith('CAM-N-001', { name: 'Gate camera', gps_lat: 32.7266, gps_lng: 74.857 })
    expect(onClose).toHaveBeenCalled()
  })

  it('offers View on map for a placed camera', () => {
    const onClose = vi.fn()
    render(wrap(<CameraEditDialog camera={makeCamera({ gps_lat: 21.2, gps_lng: 72.8 })} onClose={onClose} />))
    fireEvent.click(screen.getByTestId('view-on-map'))
    expect(screen.getByTestId('route')).toHaveTextContent('/map?focus=CAM-N-001')
    expect(onClose).toHaveBeenCalled()
  })
})

describe('FocusedCameraPanel', () => {
  it('shows the position, the fence perimeters and the camera’s virtual fences', async () => {
    const zone: Zone = {
      zone_id: 7,
      camera_id: 'CAM-N-001',
      zone_name: 'Gate line',
      zone_type: 'restricted',
      polygon: [[0, 0], [1, 0], [1, 1]],
      geo_polygon: null,
      loiter_threshold_seconds: 30,
      risk_bonus: 50,
      night_rules: { multiplier: 1.5, start: 20, end: 6 },
      allowed_persons: [],
      color_hex: '#ef4444',
      is_active: true,
      created_at: '2026-09-18T00:00:00Z',
    }
    vi.spyOn(zonesApi, 'forCamera').mockResolvedValue({ items: [zone, { ...zone, zone_id: 8, zone_name: 'Old line', is_active: false }], total: 2 })
    const onClose = vi.fn()
    render(wrap(<FocusedCameraPanel camera={makeCamera({ gps_lat: 21.216295, gps_lng: 72.86377 })} onClose={onClose} />))
    const panel = screen.getByTestId('focused-camera-panel')
    expect(panel).toHaveTextContent('21.216295, 72.863770')
    for (const ring of GEOFENCE_RINGS) expect(panel).toHaveTextContent(`${ring.radius} m`)
    expect(await screen.findByText('Gate line')).toBeInTheDocument()
    expect(screen.queryByText('Old line')).not.toBeInTheDocument()
    expect(panel).toHaveTextContent('Virtual fences on this camera (1)')
    fireEvent.click(screen.getByLabelText('Close camera focus'))
    expect(onClose).toHaveBeenCalled()
    fireEvent.click(screen.getByText('Edit fences'))
    expect(screen.getByTestId('route')).toHaveTextContent('/zones')
  })

  it('offers to locate a camera without GPS', () => {
    vi.spyOn(zonesApi, 'forCamera').mockResolvedValue({ items: [], total: 0 })
    const onLocate = vi.fn()
    render(wrap(<FocusedCameraPanel camera={makeCamera()} onClose={() => undefined} onLocate={onLocate} />))
    fireEvent.click(screen.getByTestId('focus-locate'))
    expect(onLocate).toHaveBeenCalled()
  })
})

describe('lock key release fallback', () => {
  function Harness() {
    useLockKeyReleaseFallback()
    return null
  }

  beforeEach(() => {
    useAuthStore.getState().setSession(makeToken())
  })

  it('locks when the host swallowed the Ctrl+L / Alt+L keydown', () => {
    render(<Harness />)
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keyup', { key: 'l', code: 'KeyL', altKey: true }))
    })
    expect(useAuthStore.getState().locked).toBe(true)
  })

  it('ignores the release when the keydown reached the page, plain L and Ctrl+Shift+L', () => {
    render(<Harness />)
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'l', code: 'KeyL', ctrlKey: true }))
      window.dispatchEvent(new KeyboardEvent('keyup', { key: 'l', code: 'KeyL', ctrlKey: true }))
      window.dispatchEvent(new KeyboardEvent('keyup', { key: 'l', code: 'KeyL' }))
      window.dispatchEvent(new KeyboardEvent('keyup', { key: 'L', code: 'KeyL', ctrlKey: true, shiftKey: true }))
    })
    expect(useAuthStore.getState().locked).toBe(false)
  })
})

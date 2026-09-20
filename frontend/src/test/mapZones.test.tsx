import { QueryClientProvider } from '@tanstack/react-query'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { zonesApi } from '@/api/endpoints'
import { MapZoneDialog } from '@/components/map/MapZoneDialog'
import { FocusedCameraPanel } from '@/components/map/FocusedCameraPanel'
import { createQueryClient } from '@/lib/queryClient'
import type { CameraWithAlertCount, Zone } from '@/types'

function makeCamera(overrides: Partial<CameraWithAlertCount> = {}): CameraWithAlertCount {
  return {
    camera_id: 'CAM-N-001',
    name: 'North gate',
    location_name: 'Sector 7',
    gps_lat: 32.7266,
    gps_lng: 74.857,
    rtsp_url: null,
    device_id: '0',
    camera_type: 'standard',
    zone_region: 'north',
    sector_name: null,
    status: 'online',
    registered_at: '2026-09-17T00:00:00Z',
    last_seen: '2026-09-20T00:00:00Z',
    active_alert_count: 0,
    calibrated: false,
    calibration_error_px: null,
    ...overrides,
  }
}

const POLYGON: [number, number][] = [
  [32.7266, 74.857],
  [32.7268, 74.857],
  [32.7268, 74.8574],
  [32.7266, 74.8574],
]

function wrap(children: ReactNode) {
  return (
    <QueryClientProvider client={createQueryClient()}>
      <TooltipPrimitive.Provider>
        <MemoryRouter>{children}</MemoryRouter>
      </TooltipPrimitive.Provider>
    </QueryClientProvider>
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MapZoneDialog', () => {
  it('saves the drawn polygon as a fence on a calibrated camera', async () => {
    const zone = { zone_id: 5, camera_id: 'CAM-N-001', zone_name: 'Gate line', polygon: [[1, 1]] } as unknown as Zone
    const create = vi.spyOn(zonesApi, 'create').mockResolvedValue(zone)
    const onClose = vi.fn()
    render(
      wrap(
        <MapZoneDialog
          polygon={POLYGON}
          cameras={[makeCamera({ calibrated: true, calibration_error_px: 3.2 })]}
          onClose={onClose}
          onCalibrate={() => undefined}
        />,
      ),
    )
    fireEvent.change(screen.getByLabelText('Zone name'), { target: { value: 'Gate line' } })
    fireEvent.click(screen.getByTestId('save-map-zone'))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ camera_id: 'CAM-N-001', zone_name: 'Gate line', zone_type: 'restricted', geo_polygon: POLYGON }),
    )
    // The pixel polygon is the server's job: the console never sends one for a map zone.
    expect(create.mock.calls[0]?.[0]).not.toHaveProperty('polygon')
  })

  it('explains that an uncalibrated camera cannot take a map fence, and offers to calibrate it', () => {
    const onCalibrate = vi.fn()
    const create = vi.spyOn(zonesApi, 'create')
    render(wrap(<MapZoneDialog polygon={POLYGON} cameras={[makeCamera()]} onClose={() => undefined} onCalibrate={onCalibrate} />))

    expect(screen.getByText(/No camera is calibrated against the map yet/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Calibrate CAM-N-001/ }))
    expect(onCalibrate).toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('save-map-zone'))
    expect(screen.getByTestId('map-zone-error')).toHaveTextContent('calibrated camera')
    expect(create).not.toHaveBeenCalled()
  })

  it('shows the server refusal (camera cannot see that area)', async () => {
    vi.spyOn(zonesApi, 'create').mockRejectedValue(new ApiError('Corner 2 of the zone is behind the camera', 422))
    render(
      wrap(<MapZoneDialog polygon={POLYGON} cameras={[makeCamera({ calibrated: true })]} onClose={() => undefined} onCalibrate={() => undefined} />),
    )
    fireEvent.click(screen.getByTestId('save-map-zone'))
    expect(await screen.findByTestId('map-zone-error')).toHaveTextContent('behind the camera')
  })
})

describe('FocusedCameraPanel calibration state', () => {
  it('offers calibration and shows the fit error once calibrated', () => {
    vi.spyOn(zonesApi, 'forCamera').mockResolvedValue({ items: [], total: 0 })
    const onCalibrate = vi.fn()
    const { rerender } = render(
      wrap(<FocusedCameraPanel camera={makeCamera()} onClose={() => undefined} onCalibrate={onCalibrate} />),
    )
    expect(screen.getByTestId('focus-calibrate')).toHaveTextContent('Calibrate')
    expect(screen.getByText(/Not calibrated/)).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('focus-calibrate'))
    expect(onCalibrate).toHaveBeenCalled()

    rerender(
      wrap(
        <FocusedCameraPanel
          camera={makeCamera({ calibrated: true, calibration_error_px: 4.4 })}
          onClose={() => undefined}
          onCalibrate={onCalibrate}
        />,
      ),
    )
    expect(screen.getByTestId('focus-calibrate')).toHaveTextContent('Recalibrate')
    expect(screen.getByText(/±4 px/)).toBeInTheDocument()
  })
})

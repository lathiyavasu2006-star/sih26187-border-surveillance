import { QueryClientProvider } from '@tanstack/react-query'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '@/api/client'
import { alertsApi, authApi, camerasApi, evidenceApi, statsApi } from '@/api/endpoints'
import { CameraDock } from '@/components/camera/CameraDock'
import { CameraEditDialog } from '@/components/camera/CameraEditDialog'
import { LiveAnalysisView } from '@/components/analysis/AnalysisViews'
import { AnalyzeVideoDialog } from '@/components/evidence/AnalyzeVideoDialog'
import { Sidebar } from '@/components/layout/Sidebar'
import { ScreenLock } from '@/components/modals/ScreenLock'
import { describeDirection } from '@/lib/analysis'
import { describeAccuracy, isLocalDeviceCamera } from '@/lib/geolocation'
import { livePipelineFps } from '@/lib/liveFps'
import { BORDER_REGIONS, heatPoints } from '@/lib/mapData'
import { createQueryClient } from '@/lib/queryClient'
import { istDayStartIso } from '@/lib/utils'
import { cameraEditPayload, validateAnalysisFile, validateCameraEdit, type CameraEditForm } from '@/lib/validation'
import { useAuthStore } from '@/stores/authStore'
import { useUiStore } from '@/stores/uiStore'
import { VideoAnalysisPage } from '@/pages/VideoAnalysisPage'
import { makeAlert, makeToken } from '@/test/fixtures'
import type { AnalysisJob, CameraWithAlertCount, LiveCameraState, SystemStats } from '@/types'

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
    active_alert_count: 3,
    calibrated: false,
    calibration_error_px: null,
    ...overrides,
  }
}

function wrap(children: ReactNode, extraRoutes: ReactNode = null, path = '/') {
  return (
    <QueryClientProvider client={createQueryClient()}>
      <TooltipPrimitive.Provider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/" element={children} />
            {extraRoutes}
          </Routes>
        </MemoryRouter>
      </TooltipPrimitive.Provider>
    </QueryClientProvider>
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

// --------------------------------------------------------------------------- pure helpers

describe('camera edit validation and payload', () => {
  const camera = makeCamera()
  const form = (overrides: Partial<CameraEditForm> = {}): CameraEditForm => ({
    name: 'Webcam',
    location_name: '',
    gps_lat: '',
    gps_lng: '',
    sector_name: '',
    camera_type: 'standard',
    zone_region: 'north',
    ...overrides,
  })

  it('sends only the fields that changed, GPS as a pair', () => {
    expect(cameraEditPayload(camera, form())).toEqual({})
    expect(cameraEditPayload(camera, form({ gps_lat: '32.7266', gps_lng: '74.8570', location_name: ' J&K Border - Sector 7 ' }))).toEqual({
      gps_lat: 32.7266,
      gps_lng: 74.857,
      location_name: 'J&K Border - Sector 7',
    })
    const located = makeCamera({ gps_lat: 32.7266, gps_lng: 74.857, sector_name: 'S7' })
    expect(cameraEditPayload(located, form({ gps_lat: '32.7266', gps_lng: '74.857', sector_name: '' }))).toEqual({ sector_name: null })
  })

  it('validates name and GPS like the backend', () => {
    expect(validateCameraEdit(form())).toEqual({})
    expect(validateCameraEdit(form({ name: ' ' }))).toHaveProperty('name')
    expect(validateCameraEdit(form({ gps_lat: '32.7' }))).toHaveProperty('gps_lat')
    expect(validateCameraEdit(form({ gps_lat: '91', gps_lng: '74' })).gps_lat).toMatch(/-90 and 90/)
    expect(validateCameraEdit(form({ gps_lat: '32', gps_lng: '190' })).gps_lng).toMatch(/-180 and 180/)
  })
})

describe('analysis upload validation', () => {
  it('accepts video formats up to 500 MB only', () => {
    expect(validateAnalysisFile({ name: 'patrol.MP4', size: 10 })).toBeNull()
    expect(validateAnalysisFile({ name: 'clip.mkv', size: 10 })).toBeNull()
    expect(validateAnalysisFile(null)).toMatch(/Choose/)
    expect(validateAnalysisFile({ name: 'still.jpg', size: 10 })).toMatch(/MP4, AVI, MOV or MKV/)
    expect(validateAnalysisFile({ name: 'huge.mp4', size: 501 * 1024 * 1024 })).toMatch(/500 MB/)
    expect(validateAnalysisFile({ name: 'empty.mp4', size: 0 })).toMatch(/empty/)
  })
})

describe('reporting day and live FPS', () => {
  it('computes 00:00 IST for the current reporting day', () => {
    expect(istDayStartIso(Date.parse('2026-09-18T20:00:00Z'))).toBe('2026-09-18T18:30:00.000Z')
    expect(istDayStartIso(Date.parse('2026-09-18T10:00:00Z'))).toBe('2026-09-17T18:30:00.000Z')
    expect(istDayStartIso(Date.parse('2026-09-18T18:30:00Z'))).toBe('2026-09-18T18:30:00.000Z')
  })

  it('averages the ML-reported FPS of cameras with a fresh frame', () => {
    const now = 1_000_000
    const camera = (fps: number, age: number): LiveCameraState => ({
      cameraId: 'C',
      frame: 'x',
      frameReceivedAt: now - age,
      detections: [],
      detectionsReceivedAt: 0,
      stats: { people_count: 0, vehicle_count: 0, animal_count: 0, active_alerts: 0, fps },
      viewerCount: 1,
      latencyMs: 10,
      frameCount: 5,
      measuredFps: 9,
      armedTracks: {},
    })
    expect(livePipelineFps({ a: camera(30.1, 100), b: camera(29.9, 200), stale: camera(3, 9_000) }, now)).toBe(30)
    expect(livePipelineFps({ stale: camera(3, 9_000) }, now)).toBeNull()
    expect(livePipelineFps({}, now)).toBeNull()
  })
})

describe('direction summary and map data', () => {
  const walk = (dx: number, dy: number) => Array.from({ length: 6 }, (_, i) => ({ t: i * 500, x: 100 + i * dx, y: 100 + i * dy }))

  it('describes the dominant heading, stationary tracks and fence approaches', () => {
    expect(describeDirection(walk(10, 0))).toBe('Moving east')
    expect(describeDirection(walk(0, -10))).toBe('Moving north')
    expect(describeDirection(walk(0, 0))).toBe('Stationary')
    expect(describeDirection(walk(10, 0), ['toward_fence+20'])).toBe('Toward fence')
    expect(describeDirection([])).toBe('Unknown')
  })

  it('places alerts without GPS at their camera for the heatmap', () => {
    const cameras = [makeCamera({ gps_lat: 32.7266, gps_lng: 74.857 })]
    const points = heatPoints(
      [makeAlert({ risk_score: 80 }), makeAlert({ alert_id: 'ALT-2', gps_lat: 30, gps_lng: 75, risk_score: 10 }), makeAlert({ alert_id: 'ALT-3', camera_id: 'CAM-X-999' })],
      cameras,
    )
    expect(points).toEqual([
      [32.7266, 74.857, 0.8],
      [30, 75, 0.2],
    ])
  })

  it('defines the four border regions inside India', () => {
    expect(Object.keys(BORDER_REGIONS)).toEqual(['north', 'east', 'west', 'south'])
    const north = BORDER_REGIONS.north.bounds
    expect(32.7266).toBeGreaterThan(north[0][0])
    expect(32.7266).toBeLessThan(north[1][0])
  })
})

// --------------------------------------------------------------------------- components

describe('ScreenLock', () => {
  beforeEach(() => {
    useAuthStore.getState().setSession(makeToken())
    useAuthStore.getState().lock()
  })

  it('blurs the console, shows SCREEN LOCKED and unlocks with the password', async () => {
    const login = vi.spyOn(authApi, 'login').mockResolvedValue(makeToken({ access_token: 'renewed' }))
    render(wrap(<ScreenLock onSignOut={() => undefined} />))
    const lock = screen.getByTestId('screen-lock')
    expect(lock).toHaveTextContent('SCREEN LOCKED')
    expect(lock).toHaveTextContent('SIH26187 Border Surveillance')
    expect(lock.style.backdropFilter).toBe('blur(20px)')
    expect(lock.style.background).toBe('rgba(0, 0, 0, 0.85)')
    fireEvent.change(screen.getByTestId('unlock-password'), { target: { value: 'secret-pass' } })
    fireEvent.click(screen.getByTestId('unlock-submit'))
    await waitFor(() => expect(useAuthStore.getState().locked).toBe(false))
    expect(login).toHaveBeenCalledWith('admin', 'secret-pass')
    expect(useAuthStore.getState().accessToken).toBe('renewed')
  })

  it('stays locked on a wrong password', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new ApiError('Incorrect username or password', 401))
    render(wrap(<ScreenLock onSignOut={() => undefined} />))
    fireEvent.change(screen.getByTestId('unlock-password'), { target: { value: 'nope' } })
    fireEvent.click(screen.getByTestId('unlock-submit'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect password')
    expect(useAuthStore.getState().locked).toBe(true)
  })
})

describe('CameraDock', () => {
  it('lists cameras with status, alert badge and the active camera highlighted', () => {
    useUiStore.setState({ selectedCameraId: 'CAM-N-001' })
    render(
      wrap(<CameraDock cameras={[makeCamera(), makeCamera({ camera_id: 'CAM-E-002', status: 'offline', active_alert_count: 0 })]} />, <Route path="/cameras/:id" element={<p>camera route</p>} />),
    )
    const dock = screen.getByTestId('camera-dock')
    expect(dock.style.height).toBe('80px')
    expect(dock.style.background).toBe('rgb(26, 26, 46)')
    const active = screen.getByTestId('dock-CAM-N-001')
    expect(active).toHaveAttribute('aria-current', 'true')
    expect(active.className).toContain('border-blue-500')
    expect(active).toHaveTextContent('3')
    expect(screen.getByTestId('dock-CAM-E-002')).toHaveTextContent('OFFLINE')
    fireEvent.click(screen.getByTestId('dock-CAM-E-002'))
    expect(screen.getByText('camera route')).toBeInTheDocument()
    expect(useUiStore.getState().selectedCameraId).toBe('CAM-E-002')
  })
})

describe('Sidebar', () => {
  beforeEach(() => {
    useAuthStore.getState().setSession(makeToken())
    useUiStore.setState({ sidebarMode: 'collapsed', sidebarHover: false })
    vi.spyOn(camerasApi, 'list').mockResolvedValue({ items: [makeCamera(), makeCamera({ camera_id: 'CAM-E-002', status: 'offline' })], total: 2 })
    vi.spyOn(statsApi, 'get').mockResolvedValue({
      persons_detected_today: 19,
      vehicles_detected_today: 2,
      cameras_online: 1,
      cameras_total: 2,
      cameras_offline: 1,
    } as SystemStats)
    vi.spyOn(alertsApi, 'list').mockResolvedValue({ items: [], total: 0, unacknowledged_count: 16 })
    vi.spyOn(api, 'get').mockResolvedValue({ data: { status: 'ok' } })
  })

  it('expands on hover with camera list, live stats, server latency and the user', async () => {
    const onLogout = vi.fn()
    render(wrap(<Sidebar onLogout={onLogout} />))
    const sidebar = screen.getByTestId('sidebar')
    expect(sidebar).toHaveAttribute('data-state', 'collapsed')
    fireEvent.mouseEnter(sidebar.querySelector('aside') as HTMLElement)
    await waitFor(() => expect(sidebar).toHaveAttribute('data-state', 'hover'))
    const details = screen.getByTestId('sidebar-details')
    await waitFor(() => expect(details).toHaveTextContent('CAM-E-002offline'))
    expect(details).toHaveTextContent('CAM-N-001online')
    await waitFor(() => expect(details).toHaveTextContent('Persons19'))
    expect(details).toHaveTextContent('Vehicles2')
    expect(details).toHaveTextContent('Alerts16')
    expect(details).toHaveTextContent('Online1/2')
    await waitFor(() => expect(screen.getByTestId('server-latency')).toHaveTextContent(/LOCAL\d+ ms/))
    fireEvent.click(screen.getByTestId('sidebar-logout'))
    expect(onLogout).toHaveBeenCalled()
    fireEvent.mouseLeave(sidebar.querySelector('aside') as HTMLElement)
    expect(sidebar).toHaveAttribute('data-state', 'collapsed')
  })

  it('pins at full width', () => {
    render(wrap(<Sidebar onLogout={() => undefined} />))
    fireEvent.click(screen.getByTestId('sidebar-pin'))
    expect(screen.getByTestId('sidebar')).toHaveAttribute('data-state', 'pinned')
    expect(screen.getByTestId('sidebar').style.width).toBe('220px')
  })
})

describe('CameraEditDialog', () => {
  it('saves only the changed GPS and location', async () => {
    const edit = vi.spyOn(camerasApi, 'edit').mockResolvedValue({ ...makeCamera(), gps_lat: 32.7266, gps_lng: 74.857, location_name: 'J&K Border - Sector 7' })
    const onClose = vi.fn()
    render(wrap(<CameraEditDialog camera={makeCamera()} onClose={onClose} />))
    fireEvent.change(screen.getByLabelText('GPS latitude'), { target: { value: '32.7266' } })
    fireEvent.change(screen.getByLabelText('GPS longitude'), { target: { value: '74.8570' } })
    fireEvent.change(screen.getByLabelText('Location name'), { target: { value: 'J&K Border - Sector 7' } })
    fireEvent.click(screen.getByTestId('save-camera'))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(edit).toHaveBeenCalledWith('CAM-N-001', { gps_lat: 32.7266, gps_lng: 74.857, location_name: 'J&K Border - Sector 7' })
  })

  it('refuses half a GPS pair', async () => {
    const edit = vi.spyOn(camerasApi, 'edit')
    render(wrap(<CameraEditDialog camera={makeCamera()} onClose={() => undefined} />))
    fireEvent.change(screen.getByLabelText('GPS latitude'), { target: { value: '32.7' } })
    fireEvent.click(screen.getByTestId('save-camera'))
    expect(await screen.findByText(/both latitude and longitude/)).toBeInTheDocument()
    expect(edit).not.toHaveBeenCalled()
  })
})

describe('AnalyzeVideoDialog', () => {
  const job = (overrides: Partial<AnalysisJob> = {}): AnalysisJob => ({
    job_id: 'job-1',
    evidence_id: 42,
    camera_id: 'CAM-N-001',
    standalone: false,
    status: 'running',
    percent: 40,
    frames_read: 8,
    frames_total: 20,
    persons: 1,
    vehicles: 0,
    animals: 0,
    weapons: 0,
    alerts: 0,
    video_seconds: 1,
    created_at: '2026-09-18T00:00:00Z',
    finished_at: null,
    error: null,
    summary: null,
    alerts_created: [],
    evidence_created: [],
    ...overrides,
  })
  const complete = job({
    status: 'complete',
    percent: 100,
    frames_read: 20,
    persons: 2,
    vehicles: 1,
    alerts: 2,
    alerts_created: ['ALT-1'],
    evidence_created: [7],
    summary: {
      persons_found: 2,
      vehicles_found: 1,
      animals_found: 0,
      weapons_found: {},
      weapon_sightings: {},
      alerts_detected: 2,
      alerts_created: 1,
      evidence_saved: 1,
      high_risk_moments: [{ start_seconds: 63, end_seconds: 65.5, peak_risk: 95 }],
      duration_seconds: 120,
      frames_read: 20,
      frames_processed: 20,
      fps: 10,
      resolution: [640, 480],
      zones_used: 1,
    },
  })

  it('uploads as evidence, starts the analysis, shows progress and the summary', async () => {
    const upload = vi.spyOn(evidenceApi, 'upload').mockResolvedValue({
      evidence_id: 42,
      file_hash: 'a'.repeat(64),
      file_name: 'patrol.mp4',
      file_size_bytes: 2048,
      evidence_type: 'uploaded_video',
      job_id: 'x',
      file_url: '/evidence/files/uploads/patrol.mp4',
      message: 'ok',
    })
    const analyze = vi.spyOn(evidenceApi, 'analyze').mockResolvedValue(job())
    vi.spyOn(evidenceApi, 'analysisJob').mockResolvedValue(complete)
    render(wrap(<AnalyzeVideoDialog open onOpenChange={() => undefined} cameraIds={['CAM-N-001']} />, <Route path="/alerts" element={<p>alerts route</p>} />))

    const file = new File([new Uint8Array(2048)], 'patrol.mp4', { type: 'video/mp4' })
    fireEvent.change(screen.getByTestId('analyze-file'), { target: { files: [file] } })
    fireEvent.click(screen.getByTestId('start-analysis'))

    const summary = await screen.findByTestId('analysis-summary', {}, { timeout: 5_000 })
    expect(upload).toHaveBeenCalledWith(file, { camera_id: 'CAM-N-001' }, expect.any(Function))
    expect(analyze).toHaveBeenCalledWith(42)
    expect(summary).toHaveTextContent('Persons found2')
    expect(summary).toHaveTextContent('Vehicles found1')
    expect(summary).toHaveTextContent('Alerts generated1')
    expect(summary).toHaveTextContent('1:03–1:06 · R95')
    expect(screen.getByTestId('analysis-live-stats')).toHaveTextContent('Persons2')
    fireEvent.click(screen.getByText('View Alerts'))
    expect(screen.getByText('alerts route')).toBeInTheDocument()
  })

  it('analyses a standalone video without creating evidence', async () => {
    const upload = vi.spyOn(evidenceApi, 'upload')
    const standalone = vi.spyOn(evidenceApi, 'analyzeStandalone').mockResolvedValue(job({ standalone: true, camera_id: null, evidence_id: null }))
    vi.spyOn(evidenceApi, 'analysisJob').mockResolvedValue({ ...complete, standalone: true, camera_id: null, evidence_id: null, alerts_created: [] })
    render(wrap(<AnalyzeVideoDialog open onOpenChange={() => undefined} cameraIds={['CAM-N-001']} />))
    fireEvent.change(screen.getByLabelText(/^Camera/), { target: { value: '__standalone__' } })
    fireEvent.change(screen.getByTestId('analyze-file'), { target: { files: [new File([new Uint8Array(64)], 'field.mov')] } })
    fireEvent.click(screen.getByTestId('start-analysis'))
    expect(await screen.findByText(/nothing was stored/, {}, { timeout: 5_000 })).toBeInTheDocument()
    expect(standalone).toHaveBeenCalled()
    expect(upload).not.toHaveBeenCalled()
  })

  it('rejects non-video files before uploading', async () => {
    const upload = vi.spyOn(evidenceApi, 'upload')
    render(wrap(<AnalyzeVideoDialog open onOpenChange={() => undefined} cameraIds={['CAM-N-001']} />))
    fireEvent.change(screen.getByTestId('analyze-file'), { target: { files: [new File(['x'], 'notes.jpg')] } })
    await act(async () => {
      fireEvent.click(screen.getByTestId('start-analysis'))
    })
    expect(screen.getByRole('alert')).toHaveTextContent('MP4, AVI, MOV or MKV')
    expect(upload).not.toHaveBeenCalled()
  })
})

// --------------------------------------------------------------------------- follow-up: location, live analysis

describe('camera location', () => {
  it('recognises cameras attached to the console', () => {
    expect(isLocalDeviceCamera({ device_id: '0', rtsp_url: null })).toBe(true)
    expect(isLocalDeviceCamera({ device_id: null, rtsp_url: '1' })).toBe(true)
    expect(isLocalDeviceCamera({ device_id: null, rtsp_url: 'rtsp://10.0.0.2/s' })).toBe(false)
    expect(describeAccuracy(35)).toBe('±35 m')
    expect(describeAccuracy(2400)).toBe('±2.4 km')
  })
})

describe('LiveAnalysisView', () => {
  it('shows the streamed frame with live counters', () => {
    const job = {
      job_id: 'j', evidence_id: null, camera_id: null, standalone: true, status: 'running' as const, percent: 42, frames_read: 10, frames_total: 24,
      persons: 3, vehicles: 1, animals: 0, weapons: 0, alerts: 2, video_seconds: 75, created_at: '', finished_at: null, error: null, summary: null,
      alerts_created: [], evidence_created: [],
    }
    render(<LiveAnalysisView job={job} frame="/9j/AAAA" live />)
    const view = screen.getByTestId('analysis-live-view')
    expect(view.querySelector('img')?.getAttribute('src')).toBe('data:image/jpeg;base64,/9j/AAAA')
    expect(view).toHaveTextContent('LIVE ANALYSIS')
    expect(view).toHaveTextContent('42% · 1m 15s')
    expect(view).toHaveTextContent('PERSONS 3')
    expect(view).toHaveTextContent('ALERTS 2')
  })

  it('explains the idle state', () => {
    render(<LiveAnalysisView job={null} frame={null} live={false} />)
    expect(screen.getByTestId('analysis-live-view')).toHaveTextContent('NO ANALYSIS RUNNING')
  })
})

describe('VideoAnalysisPage', () => {
  beforeEach(() => {
    useAuthStore.getState().setSession(makeToken())
    vi.spyOn(camerasApi, 'list').mockResolvedValue({ items: [makeCamera()], total: 1 })
  })

  it('analyses a dropped video without any camera and shows the result', async () => {
    const running = {
      job_id: 'job-live', evidence_id: null, camera_id: null, standalone: true, status: 'running' as const, percent: 10, frames_read: 5,
      frames_total: 50, persons: 1, vehicles: 0, animals: 0, weapons: 0, alerts: 0, video_seconds: 1, created_at: '', finished_at: null, error: null,
      summary: null, alerts_created: [], evidence_created: [],
    }
    const standalone = vi.spyOn(evidenceApi, 'analyzeStandalone').mockResolvedValue(running)
    vi.spyOn(evidenceApi, 'analysisJob').mockResolvedValue({
      ...running,
      status: 'complete',
      percent: 100,
      frames_read: 50,
      summary: {
        persons_found: 1, vehicles_found: 0, animals_found: 0, weapons_found: {}, weapon_sightings: {}, alerts_detected: 0, alerts_created: 0, evidence_saved: 0,
        high_risk_moments: [], duration_seconds: 10, frames_read: 50, frames_processed: 10, fps: 5, resolution: [640, 480], zones_used: 0,
      },
    })
    const upload = vi.spyOn(evidenceApi, 'upload')
    render(wrap(<VideoAnalysisPage />))
    expect(screen.getByLabelText('Camera (optional)')).toHaveValue('__standalone__')
    const file = new File([new Uint8Array(64)], 'phone-clip.mp4', { type: 'video/mp4' })
    fireEvent.drop(screen.getByTestId('analysis-dropzone'), { dataTransfer: { files: [file] } })
    expect(screen.getByTestId('analysis-dropzone')).toHaveTextContent('phone-clip.mp4')
    fireEvent.click(screen.getByTestId('analysis-start'))
    expect(await screen.findByTestId('analysis-summary', {}, { timeout: 6_000 })).toHaveTextContent('Persons found1')
    expect(standalone).toHaveBeenCalledWith(file, expect.any(Function))
    expect(upload).not.toHaveBeenCalled()
    expect(screen.getByText('Analyze another')).toBeInTheDocument()
  })
})

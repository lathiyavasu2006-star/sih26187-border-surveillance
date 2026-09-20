import { api, cleanParams } from '@/api/client'
import type {
  Alert,
  AlertAcknowledgeRequest,
  AlertFilters,
  AlertListResponse,
  AlertStats,
  AnalysisJob,
  AuditLogEntry,
  AuditLogFilters,
  Camera,
  CalibrationRequest,
  CameraCalibration,
  CameraDeleteResponse,
  CameraDetail,
  CameraEditRequest,
  CameraRegisterRequest,
  CameraRegisterResponse,
  CameraStatus,
  CameraStatusResponse,
  CameraWithAlertCount,
  ClearTestDataResponse,
  Evidence,
  EvidenceArchiveResponse,
  EvidenceFilters,
  EvidenceUploadResponse,
  EvidenceVerifyResponse,
  EventFilters,
  Hardware,
  HardwareRegisterRequest,
  HardwareStatusMap,
  HardwareTestResponse,
  HealthResponse,
  HostLocation,
  MessageResponse,
  Page,
  StreamTestResponse,
  SystemHealthSample,
  SystemStats,
  Timeline,
  TokenResponse,
  TrackEvent,
  TrackHistory,
  UserProfile,
  Zone,
  ZoneCreateRequest,
  ZoneUpdateRequest,
} from '@/types'

// ------------------------------------------------------------------ auth

export const authApi = {
  /** POST /auth/login — OAuth2 password form (application/x-www-form-urlencoded). */
  async login(username: string, password: string): Promise<TokenResponse> {
    const form = new URLSearchParams()
    form.set('username', username)
    form.set('password', password)
    const { data } = await api.post<TokenResponse>('/auth/login', form, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    return data
  },
  async me(): Promise<UserProfile> {
    return (await api.get<UserProfile>('/auth/me')).data
  },
  async logout(refreshToken: string | null): Promise<MessageResponse> {
    return (await api.post<MessageResponse>('/auth/logout', { refresh_token: refreshToken })).data
  },
  async changePassword(currentPassword: string, newPassword: string): Promise<MessageResponse> {
    return (
      await api.post<MessageResponse>('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      })
    ).data
  },
}

// ------------------------------------------------------------------ cameras

export const camerasApi = {
  async list(params: { zone_region?: string; status?: CameraStatus; camera_type?: string } = {}): Promise<Page<CameraWithAlertCount>> {
    return (await api.get<Page<CameraWithAlertCount>>('/cameras', { params: cleanParams({ ...params, limit: 1000 }) })).data
  },
  async get(cameraId: string): Promise<CameraDetail> {
    return (await api.get<CameraDetail>(`/cameras/${encodeURIComponent(cameraId)}`)).data
  },
  async register(payload: CameraRegisterRequest): Promise<CameraRegisterResponse> {
    return (await api.post<CameraRegisterResponse>('/cameras/register', payload, { timeout: 60_000 })).data
  },
  /** PATCH /cameras/{id}: only the fields present are changed; gps_lat/gps_lng travel together. */
  async edit(cameraId: string, payload: CameraEditRequest): Promise<Camera> {
    return (await api.patch<Camera>(`/cameras/${encodeURIComponent(cameraId)}`, payload)).data
  },
  /** Position of the console host (Windows location service); 503 with the reason when location is off. */
  async hostLocation(refresh = false): Promise<HostLocation> {
    return (await api.get<HostLocation>('/cameras/host-location', { params: refresh ? { refresh: true } : {}, timeout: 45_000 })).data
  },
  /** Fit the camera's ground plane from landmarks marked on the image and the map (supervisor+). */
  async calibrate(cameraId: string, payload: CalibrationRequest): Promise<CameraCalibration> {
    return (await api.put<CameraCalibration>(`/cameras/${encodeURIComponent(cameraId)}/calibration`, payload)).data
  },
  async calibration(cameraId: string): Promise<CameraCalibration> {
    return (await api.get<CameraCalibration>(`/cameras/${encodeURIComponent(cameraId)}/calibration`)).data
  },
  async removeCalibration(cameraId: string): Promise<MessageResponse> {
    return (await api.delete<MessageResponse>(`/cameras/${encodeURIComponent(cameraId)}/calibration`)).data
  },
  async setStatus(cameraId: string, status: CameraStatus): Promise<CameraStatusResponse> {
    return (await api.patch<CameraStatusResponse>(`/cameras/${encodeURIComponent(cameraId)}/status`, { status })).data
  },
  async remove(cameraId: string): Promise<CameraDeleteResponse> {
    return (await api.delete<CameraDeleteResponse>(`/cameras/${encodeURIComponent(cameraId)}`)).data
  },
  async test(cameraId: string): Promise<StreamTestResponse> {
    return (await api.get<StreamTestResponse>(`/cameras/${encodeURIComponent(cameraId)}/test`, { timeout: 60_000 })).data
  },
}

// ------------------------------------------------------------------ alerts

export const alertsApi = {
  async list(filters: AlertFilters = {}): Promise<AlertListResponse> {
    return (await api.get<AlertListResponse>('/alerts', { params: cleanParams(filters) })).data
  },
  async stats(): Promise<AlertStats> {
    return (await api.get<AlertStats>('/alerts/stats')).data
  },
  async get(alertId: string): Promise<Alert> {
    return (await api.get<Alert>(`/alerts/${encodeURIComponent(alertId)}`)).data
  },
  async acknowledge(alertId: string, payload: AlertAcknowledgeRequest): Promise<Alert> {
    return (await api.patch<Alert>(`/alerts/${encodeURIComponent(alertId)}/acknowledge`, payload)).data
  },
  async remove(alertId: string): Promise<void> {
    await api.delete(`/alerts/${encodeURIComponent(alertId)}`)
  },
  /** Admin: acknowledges every open alert raised before today (IST) as a false alarm; nothing is deleted. */
  async clearTestData(): Promise<ClearTestDataResponse> {
    return (await api.post<ClearTestDataResponse>('/alerts/clear-test-data')).data
  },
}

// ------------------------------------------------------------------ events

export const eventsApi = {
  async list(filters: EventFilters = {}): Promise<Page<TrackEvent>> {
    return (await api.get<Page<TrackEvent>>('/events', { params: cleanParams(filters) })).data
  },
  async active(cameraId?: string): Promise<Page<TrackEvent>> {
    return (await api.get<Page<TrackEvent>>('/events/active', { params: cleanParams({ camera_id: cameraId }) })).data
  },
  async history(trackId: number, cameraId?: string): Promise<TrackHistory> {
    return (await api.get<TrackHistory>(`/events/${trackId}`, { params: cleanParams({ camera_id: cameraId }) })).data
  },
  async timeline(trackId: number, cameraId?: string): Promise<Timeline> {
    return (await api.get<Timeline>(`/events/${trackId}/timeline`, { params: cleanParams({ camera_id: cameraId }) })).data
  },
}

// ------------------------------------------------------------------ evidence

export const evidenceApi = {
  async list(filters: EvidenceFilters = {}): Promise<Page<Evidence>> {
    return (await api.get<Page<Evidence>>('/evidence', { params: cleanParams(filters) })).data
  },
  async get(evidenceId: number): Promise<Evidence> {
    return (await api.get<Evidence>(`/evidence/${evidenceId}`)).data
  },
  async verify(evidenceId: number): Promise<EvidenceVerifyResponse> {
    return (await api.get<EvidenceVerifyResponse>(`/evidence/${evidenceId}/verify`, { timeout: 120_000 })).data
  },
  async archive(evidenceId: number): Promise<EvidenceArchiveResponse> {
    return (await api.post<EvidenceArchiveResponse>(`/evidence/${evidenceId}/archive`)).data
  },
  async remove(evidenceId: number): Promise<MessageResponse> {
    return (await api.delete<MessageResponse>(`/evidence/${evidenceId}`)).data
  },
  /** Request path of the browser-playable (H.264) version of a video evidence item. */
  playbackPath(evidenceId: number): string {
    return `/evidence/${evidenceId}/playback`
  },
  async analyze(evidenceId: number): Promise<AnalysisJob> {
    return (await api.post<AnalysisJob>(`/evidence/${evidenceId}/analyze`)).data
  },
  async analysisStatus(evidenceId: number): Promise<AnalysisJob> {
    return (await api.get<AnalysisJob>(`/evidence/${evidenceId}/analysis-status`)).data
  },
  async analysisJob(jobId: string): Promise<AnalysisJob> {
    return (await api.get<AnalysisJob>(`/evidence/analysis-jobs/${encodeURIComponent(jobId)}`)).data
  },
  async analyzeStandalone(file: File, onProgress?: (percent: number) => void): Promise<AnalysisJob> {
    const form = new FormData()
    form.append('file', file)
    const { data } = await api.post<AnalysisJob>('/evidence/analyze-standalone', form, {
      timeout: 30 * 60_000,
      onUploadProgress: (event) => {
        if (onProgress && event.total) onProgress(Math.round((event.loaded / event.total) * 100))
      },
    })
    return data
  },
  async upload(
    file: File,
    fields: { camera_id: string; alert_id?: string; track_id?: number },
    onProgress?: (percent: number) => void,
  ): Promise<EvidenceUploadResponse> {
    const form = new FormData()
    form.append('file', file)
    form.append('camera_id', fields.camera_id)
    if (fields.alert_id) form.append('alert_id', fields.alert_id)
    if (fields.track_id !== undefined) form.append('track_id', String(fields.track_id))
    const { data } = await api.post<EvidenceUploadResponse>('/evidence/upload', form, {
      timeout: 30 * 60_000,
      onUploadProgress: (event) => {
        if (onProgress && event.total) onProgress(Math.round((event.loaded / event.total) * 100))
      },
    })
    return data
  },
}

// ------------------------------------------------------------------ zones

export const zonesApi = {
  async list(params: { camera_id?: string; zone_type?: string; is_active?: boolean } = {}): Promise<Page<Zone>> {
    return (await api.get<Page<Zone>>('/zones', { params: cleanParams({ ...params, limit: 1000 }) })).data
  },
  async forCamera(cameraId: string, includeInactive = false): Promise<Page<Zone>> {
    return (
      await api.get<Page<Zone>>(`/zones/${encodeURIComponent(cameraId)}`, {
        params: { include_inactive: includeInactive },
      })
    ).data
  },
  async create(payload: ZoneCreateRequest): Promise<Zone> {
    return (await api.post<Zone>('/zones', payload)).data
  },
  async update(zoneId: number, payload: ZoneUpdateRequest): Promise<Zone> {
    return (await api.put<Zone>(`/zones/${zoneId}`, payload)).data
  },
  async remove(zoneId: number): Promise<MessageResponse> {
    return (await api.delete<MessageResponse>(`/zones/${zoneId}`)).data
  },
}

// ------------------------------------------------------------------ hardware

export const hardwareApi = {
  async status(): Promise<HardwareStatusMap> {
    return (await api.get<HardwareStatusMap>('/hardware/status')).data
  },
  async register(payload: HardwareRegisterRequest): Promise<Hardware> {
    return (await api.post<Hardware>('/hardware/register', payload)).data
  },
  async testConnection(hardwareId: number): Promise<HardwareTestResponse> {
    return (await api.post<HardwareTestResponse>(`/hardware/${hardwareId}/test-connection`, undefined, { timeout: 60_000 })).data
  },
  async update(hardwareId: number, payload: Partial<HardwareRegisterRequest>): Promise<Hardware> {
    return (await api.patch<Hardware>(`/hardware/${hardwareId}`, payload)).data
  },
  async remove(hardwareId: number): Promise<void> {
    await api.delete(`/hardware/${hardwareId}`)
  },
}

// ------------------------------------------------------------------ stats

export const statsApi = {
  async get(): Promise<SystemStats> {
    return (await api.get<SystemStats>('/stats')).data
  },
  async health(): Promise<HealthResponse> {
    return (await api.get<HealthResponse>('/health')).data
  },
  async auditLog(filters: AuditLogFilters = {}): Promise<Page<AuditLogEntry>> {
    return (await api.get<Page<AuditLogEntry>>('/stats/audit-log', { params: cleanParams(filters) })).data
  },
  async healthHistory(hours = 24): Promise<Page<SystemHealthSample>> {
    return (await api.get<Page<SystemHealthSample>>('/stats/system-health-history', { params: { hours } })).data
  },
}

export type { Camera }

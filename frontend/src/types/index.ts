// Types mirror the FastAPI response models in backend/schemas (Week 1/2). Keep them in sync with the
// Pydantic definitions: every field here exists on the wire, nothing is invented client side.

export type CameraType = 'standard' | 'ptz' | 'thermal' | 'drone' | 'radar' | 'scanner' | 'satellite'
export type ZoneRegion = 'north' | 'south' | 'east' | 'west'
export type CameraStatus = 'online' | 'offline' | 'degraded'
export type UserRole = 'admin' | 'regional_head' | 'supervisor' | 'operator'
export type ZoneType = 'public' | 'buffer' | 'sensitive' | 'restricted' | 'no_mans_land'
export type AlertType =
  | 'intrusion'
  | 'loitering'
  | 'weapon'
  | 'vehicle'
  | 'behavior'
  | 'camera_offline'
  | 'zone_breach'
  | 'animal'
  | 'smoke'
  | 'fence_damage'
export type RiskLevel = 'normal' | 'low' | 'suspicious' | 'high_risk' | 'critical'
export type EvidenceType = 'snapshot' | 'video_clip' | 'manual_snapshot' | 'uploaded_video'
export type HardwareType =
  | 'standard_camera'
  | 'ptz_camera'
  | 'thermal_camera'
  | 'drone'
  | 'radar'
  | 'license_plate_scanner'
  | 'satellite'
export type HardwareStatus = 'connected' | 'disconnected' | 'error' | 'standby'
export type Direction =
  | 'stationary'
  | 'north'
  | 'south'
  | 'east'
  | 'west'
  | 'northeast'
  | 'northwest'
  | 'southeast'
  | 'southwest'
export type AuditStatus = 'success' | 'failure' | 'denied' | 'error'

/** JSON value as produced by the backend for free-form columns (positions, night_rules, configs). */
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }
export type JsonObject = { [key: string]: JsonValue }

export interface Page<T> {
  items: T[]
  total: number
}

// ------------------------------------------------------------------ auth

export interface LoginUserInfo {
  user_id: string
  username: string
  role: UserRole
  camera_access: string[]
  zone_access: string[]
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  expires_in: number
  user: LoginUserInfo
}

export interface AccessTokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
}

export interface UserProfile extends LoginUserInfo {
  is_active: boolean
  created_at: string
  last_login: string | null
  failed_login_attempts: number
  locked_until: string | null
}

export interface MessageResponse {
  message: string
}

// ------------------------------------------------------------------ cameras

export interface Camera {
  camera_id: string
  name: string
  location_name: string | null
  gps_lat: number | null
  gps_lng: number | null
  rtsp_url: string | null
  device_id: string | null
  camera_type: CameraType
  zone_region: ZoneRegion
  sector_name: string | null
  status: CameraStatus
  registered_at: string
  last_seen: string
  /** A ground-plane calibration exists, so zones drawn on the map project into this camera's frame. */
  calibrated: boolean
  calibration_error_px: number | null
}

/** One landmark marked in both views: [x, y] in camera pixels and [lat, lng] on the map. */
export interface CalibrationPoint {
  image: [number, number]
  geo: [number, number]
}

export interface CameraCalibration {
  camera_id: string
  points: CalibrationPoint[]
  image_size: [number, number]
  error_px: number
  calibrated_at: string
  zones_projected: number
}

export interface CalibrationRequest {
  points: CalibrationPoint[]
  image_size: [number, number]
}

export interface CameraWithAlertCount extends Camera {
  active_alert_count: number
}

export interface CameraDetail extends Camera {
  active_alert_count: number
  viewer_count: number
  recent_alerts: Alert[]
}

export interface CameraRegisterRequest {
  name: string
  location_name?: string | null
  gps_lat?: number | null
  gps_lng?: number | null
  rtsp_url?: string | null
  device_id?: string | null
  camera_type: CameraType
  zone_region: ZoneRegion
  sector_name?: string | null
}

export interface CameraRegisterResponse extends Camera {
  connection_ok: boolean
  preview_frame: string | null
  message: string
}

export interface CameraStatusResponse extends Camera {
  previous_status: CameraStatus
  offline_alert_id: string | null
}

export interface CameraDeleteResponse {
  camera_id: string
  deletion: 'hard' | 'soft'
  message: string
}

export interface StreamTestResponse {
  camera_id: string | null
  connected: boolean
  fps: number
  resolution: string | null
  frame_preview: string | null
  latency_ms: number
  message: string
}

// ------------------------------------------------------------------ alerts

export interface Alert {
  alert_id: string
  camera_id: string
  track_id: number | null
  person_uuid: string | null
  alert_type: AlertType
  risk_score: number
  risk_level: RiskLevel
  risk_reasons: string[]
  snapshot_path: string | null
  video_clip_path: string | null
  gps_lat: number | null
  gps_lng: number | null
  zone_name: string | null
  zone_type: string | null
  timestamp: string
  acknowledged: boolean
  acknowledged_by: string | null
  acknowledged_at: string | null
  false_alarm: boolean
  notes: string | null
}

export interface AlertListResponse extends Page<Alert> {
  unacknowledged_count: number
}

export interface AlertFilters {
  camera_id?: string
  alert_type?: AlertType
  risk_level?: RiskLevel
  date_from?: string
  date_to?: string
  acknowledged?: boolean
  skip?: number
  limit?: number
}

export interface AlertAcknowledgeRequest {
  acknowledged_by: string
  false_alarm: boolean
  notes?: string | null
}

export interface AlertStats {
  total_today: number
  critical_today: number
  unacknowledged: number
  by_type: Record<string, number>
  by_risk_level: Record<string, number>
  by_camera: { camera_id: string; count: number }[]
  hourly_trend: { hour: number; count: number }[]
  timezone: string
}

// ------------------------------------------------------------------ events

export interface TrackEvent {
  event_id: number
  track_id: number
  person_uuid: string | null
  camera_id: string
  object_class: string
  first_seen: string
  last_seen: string
  total_time_seconds: number
  positions: JsonObject[]
  alert_count: number
  max_risk_score: number
  zone_history: JsonObject[]
  is_active: boolean
}

export interface EventFilters {
  camera_id?: string
  object_class?: string
  date_from?: string
  date_to?: string
  is_active?: boolean
  skip?: number
  limit?: number
}

export interface TrackHistory {
  track_id: number
  events: TrackEvent[]
  total_events: number
  total_time_seconds: number
  alert_count: number
  max_risk_score: number
  cameras: string[]
}

export interface TimelinePoint {
  timestamp: string
  camera_id: string
  cx: number | null
  cy: number | null
  zone_name: string | null
  zone_type: string | null
  risk_score: number
  risk_level: string | null
  object_class: string | null
}

export interface Timeline {
  track_id: number
  source: 'tracked_objects' | 'event_positions'
  points: TimelinePoint[]
}

// ------------------------------------------------------------------ evidence

export interface Evidence {
  evidence_id: number
  alert_id: string | null
  camera_id: string
  track_id: number | null
  evidence_type: EvidenceType
  file_path: string
  file_name: string | null
  file_size_bytes: number | null
  file_hash: string
  duration_seconds: number | null
  gps_lat: number | null
  gps_lng: number | null
  created_at: string
  is_hot_storage: boolean
  archived_at: string | null
  file_url: string
  file_exists: boolean
}

export interface EvidenceFilters {
  camera_id?: string
  evidence_type?: EvidenceType
  alert_id?: string
  is_hot_storage?: boolean
  date_from?: string
  date_to?: string
  skip?: number
  limit?: number
}

export interface EvidenceUploadResponse {
  evidence_id: number
  file_hash: string
  file_name: string
  file_size_bytes: number
  evidence_type: string
  job_id: string
  file_url: string
  message: string
}

export interface EvidenceVerifyResponse {
  evidence_id: number
  stored_hash: string
  computed_hash: string | null
  integrity: 'valid' | 'tampered' | 'missing'
  verified_at: string
}

export interface EvidenceArchiveResponse {
  evidence_id: number
  is_hot_storage: boolean
  archived_at: string
  file_path: string
  message: string
}

// ------------------------------------------------------------------ zones

export interface NightRules {
  multiplier: number
  start: number
  end: number
}

/** GET /cameras/host-location: the console host's position from the Windows location service. */
export interface HostLocation {
  lat: number
  lng: number
  accuracy_m: number | null
  source: 'windows-location-service'
  captured_at: string
}

export interface Zone {
  zone_id: number
  camera_id: string
  zone_name: string
  zone_type: ZoneType
  polygon: [number, number][]
  /** Set when the zone was drawn on the map; the pixel polygon above is projected from it. */
  geo_polygon: [number, number][] | null
  loiter_threshold_seconds: number
  risk_bonus: number
  night_rules: NightRules
  allowed_persons: string[]
  color_hex: string
  is_active: boolean
  created_at: string
}

export interface ZoneCreateRequest {
  camera_id: string
  zone_name: string
  zone_type: ZoneType
  /** Send polygon (camera pixels) or geo_polygon (drawn on the map); the server projects the latter. */
  polygon?: [number, number][]
  geo_polygon?: [number, number][]
  loiter_threshold_seconds: number
  night_rules: NightRules
  allowed_persons: string[]
  color_hex: string
  is_active: boolean
}

export type ZoneUpdateRequest = Partial<Omit<ZoneCreateRequest, 'camera_id'>>

// ------------------------------------------------------------------ hardware

export interface Hardware {
  hardware_id: number
  hardware_type: HardwareType
  name: string
  model_number: string | null
  manufacturer: string | null
  connection_config: JsonObject
  status: HardwareStatus
  camera_id: string | null
  capabilities: string[]
  last_seen: string | null
  firmware_version: string | null
  registered_at: string
  notes: string | null
}

export interface HardwareTypeSummary {
  total: number
  connected: number
  disconnected: number
  error: number
  standby: number
  items: Hardware[]
}

export type HardwareStatusMap = Record<string, HardwareTypeSummary>

export interface HardwareRegisterRequest {
  hardware_type: HardwareType
  name: string
  model_number?: string | null
  manufacturer?: string | null
  connection_config: JsonObject
  status: HardwareStatus
  camera_id?: string | null
  capabilities: string[]
  firmware_version?: string | null
  notes?: string | null
}

export interface HardwareTestResponse {
  hardware_id: number
  connected: boolean
  latency_ms: number
  status: HardwareStatus
  message: string
}

// ------------------------------------------------------------------ stats

export interface SystemStats {
  persons_detected_today: number
  vehicles_detected_today: number
  active_alerts: number
  critical_alerts: number
  cameras_online: number
  cameras_total: number
  cameras_offline: number
  cameras_degraded: number
  avg_system_fps: number
  cpu_percent: number
  ram_percent: number
  gpu_percent: number | null
  gpu_memory_used_mb: number | null
  gpu_memory_total_mb: number | null
  disk_free_gb: number
  uptime_seconds: number
  websocket_clients: number
  timestamp: string
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'down'
  db: 'connected' | 'disconnected'
  camera_monitor: 'running' | 'stopped' | 'disabled'
  timestamp: string
}

export interface AuditLogEntry {
  log_id: number
  user_id: string | null
  username: string | null
  action: string
  table_name: string | null
  record_id: string | null
  old_value: JsonObject | null
  new_value: JsonObject | null
  ip_address: string | null
  user_agent: string | null
  session_id: string | null
  status: string
  timestamp: string
}

export interface AuditLogFilters {
  action?: string
  status?: AuditStatus
  date_from?: string
  date_to?: string
  skip?: number
  limit?: number
}

export interface SystemHealthSample {
  id: number
  server_name: string
  region: string | null
  cpu_percent: number | null
  ram_percent: number | null
  ram_used_gb: number | null
  ram_total_gb: number | null
  gpu_percent: number | null
  gpu_memory_used_mb: number | null
  gpu_memory_total_mb: number | null
  disk_used_gb: number | null
  disk_free_gb: number | null
  avg_fps: number
  cameras_online: number
  cameras_total: number
  active_alerts: number
  critical_alerts: number
  persons_detected: number
  vehicles_detected: number
  timestamp: string
}

// ------------------------------------------------------------------ WebSocket

export interface WSDetection {
  track_id: number
  object_class: string
  confidence: number | null
  cx: number | null
  cy: number | null
  bbox_x1: number | null
  bbox_y1: number | null
  bbox_x2: number | null
  bbox_y2: number | null
  in_fence: boolean
  zone_name: string | null
  zone_type: ZoneType | null
  loitering: boolean
  time_in_zone_seconds: number
  direction: Direction
  risk_score: number
  risk_level: RiskLevel
  person_uuid: string | null
  /** Set when this person is holding a weapon (firearm / knife). */
  weapon_class?: string | null
  weapon_confidence?: number | null
}

export interface WSStats {
  people_count: number
  vehicle_count: number
  animal_count: number
  active_alerts: number
  fps: number
}

export interface WSConnected {
  type: 'connected'
  camera_id: string
  user_id: string
  role: UserRole
  may_ingest_frames: boolean
  viewer_count: number
  timestamp: string
}

export interface WSFrameUpdate {
  type: 'frame_update'
  camera_id: string
  frame: string | null
  detections: WSDetection[]
  alerts: Alert[]
  stats: WSStats
  timestamp: string
  viewer_count: number
  server_timestamp: string
}

export interface WSPong {
  type: 'pong'
  timestamp: string
}

export interface WSError {
  type: 'error'
  message: string
}

export interface WSAcknowledgeOk {
  type: 'acknowledge_ok'
  alert: Alert
  timestamp: string
}

export interface WSCameraOffline {
  type: 'camera_offline'
  severity: 'warning'
  camera_id: string
  sector_name: string | null
  zone_region: string | null
  alert_id: string | null
  reason: string | null
  message: string
  sound: string
  timestamp: string
}

export interface WSCameraOnline {
  type: 'camera_online'
  severity: 'info'
  camera_id: string
  sector_name: string | null
  message: string
  timestamp: string
}

export interface WSCriticalAlert {
  type: 'critical_alert'
  severity: 'critical'
  alert_id: string
  camera_id: string
  alert_type: string
  risk_score: number
  risk_reasons: string[]
  message: string
  sound: string
  timestamp: string
}

export interface WSAlertAcknowledged {
  type: 'alert_acknowledged'
  alert_id: string
  camera_id: string
  acknowledged_by: string
  acknowledged_by_username: string | null
  false_alarm: boolean
  timestamp: string
}

export interface WSZoneUpdate {
  type: 'zone_update'
  camera_id: string
  zone_id: number
  action: string
  zone: JsonObject | null
  message: string
  timestamp: string
}

export interface WSZoneDeleted {
  type: 'zone_deleted'
  camera_id: string
  zone_id: number
  message: string
  timestamp: string
}

export interface WSNeighborOffline {
  type: 'neighbor_offline_alert'
  offline_camera_id: string
  message: string
  timestamp: string
}

export type WSServerMessage =
  | WSConnected
  | WSFrameUpdate
  | WSPong
  | WSError
  | WSAcknowledgeOk
  | WSCameraOffline
  | WSCameraOnline
  | WSCriticalAlert
  | WSAlertAcknowledged
  | WSZoneUpdate
  | WSZoneDeleted
  | WSNeighborOffline

export type WSConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed' | 'denied'

/** Latest live state of one camera, fed by frame_update messages. */
export interface LiveCameraState {
  cameraId: string
  frame: string | null
  frameReceivedAt: number
  detections: WSDetection[]
  detectionsReceivedAt: number
  stats: WSStats | null
  viewerCount: number
  /** Milliseconds between the ML timestamp and arrival in the browser. */
  latencyMs: number | null
  frameCount: number
  measuredFps: number
  /** Weapon type -> track ids seen carrying it since this console started watching the camera. */
  armedTracks: Record<string, number[]>
}

// ------------------------------------------------------------------ Week 4 follow-up endpoints

export interface CameraEditRequest {
  name?: string
  location_name?: string | null
  gps_lat?: number | null
  gps_lng?: number | null
  sector_name?: string | null
  camera_type?: CameraType
  zone_region?: ZoneRegion
}

export interface ClearTestDataResponse {
  cleared: number
  before: string
  message: string
}

export interface HighRiskMoment {
  start_seconds: number
  end_seconds: number
  peak_risk: number
}

export interface AnalysisSummary {
  persons_found: number
  vehicles_found: number
  animals_found: number
  /** weapon type -> distinct people seen carrying it */
  weapons_found: Record<string, number>
  /** weapon type -> analysed frames it appeared in */
  weapon_sightings: Record<string, number>
  alerts_detected: number
  alerts_created: number
  evidence_saved: number
  high_risk_moments: HighRiskMoment[]
  duration_seconds: number | null
  frames_read: number | null
  frames_processed: number | null
  fps: number | null
  resolution: number[] | null
  zones_used: number | null
}

export type AnalysisStatus = 'queued' | 'loading' | 'running' | 'complete' | 'failed'

export interface AnalysisJob {
  job_id: string
  evidence_id: number | null
  camera_id: string | null
  standalone: boolean
  status: AnalysisStatus
  percent: number
  frames_read: number
  frames_total: number
  persons: number
  vehicles: number
  animals: number
  /** people seen carrying a weapon so far */
  weapons: number
  alerts: number
  video_seconds: number
  created_at: string
  finished_at: string | null
  error: string | null
  summary: AnalysisSummary | null
  alerts_created: string[]
  evidence_created: number[]
}

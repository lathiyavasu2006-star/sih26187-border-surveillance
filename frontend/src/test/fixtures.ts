import type { Alert, TokenResponse, TrackEvent, WSDetection, WSFrameUpdate, Zone } from '@/types'

export function makeDetection(overrides: Partial<WSDetection> = {}): WSDetection {
  return {
    track_id: 7,
    object_class: 'person',
    confidence: 0.87,
    cx: 150,
    cy: 200,
    bbox_x1: 100,
    bbox_y1: 100,
    bbox_x2: 200,
    bbox_y2: 300,
    in_fence: true,
    zone_name: 'Gate',
    zone_type: 'restricted',
    loitering: false,
    time_in_zone_seconds: 12,
    direction: 'north',
    risk_score: 60,
    risk_level: 'suspicious',
    person_uuid: null,
    ...overrides,
  }
}

export function makeAlert(overrides: Partial<Alert> = {}): Alert {
  return {
    alert_id: 'ALT-20260917063723-6564bb83e3bb4181',
    camera_id: 'CAM-N-001',
    track_id: 1,
    person_uuid: null,
    alert_type: 'loitering',
    risk_score: 80,
    risk_level: 'high_risk',
    risk_reasons: ['zone_restricted+50', 'loitering+20(threshold:30s,dwell:41s)'],
    snapshot_path: 'E:/sih26187/evidence/snapshots/ALT-20260917063723-6564bb83e3bb4181.jpg',
    video_clip_path: null,
    gps_lat: null,
    gps_lng: null,
    zone_name: 'Gate',
    zone_type: 'restricted',
    timestamp: '2026-09-17T06:37:23+00:00',
    acknowledged: false,
    acknowledged_by: null,
    acknowledged_at: null,
    false_alarm: false,
    notes: null,
    ...overrides,
  }
}

export function makeZone(overrides: Partial<Zone> = {}): Zone {
  return {
    zone_id: 1,
    camera_id: 'CAM-N-001',
    zone_name: 'Gate',
    zone_type: 'restricted',
    geo_polygon: null,
  polygon: [
      [300, 100],
      [500, 100],
      [500, 300],
      [300, 300],
    ],
    loiter_threshold_seconds: 30,
    risk_bonus: 50,
    night_rules: { multiplier: 1.5, start: 22, end: 5 },
    allowed_persons: [],
    color_hex: '#ef4444',
    is_active: true,
    created_at: '2026-09-17T00:00:00+00:00',
    ...overrides,
  }
}

export function makeEvent(overrides: Partial<TrackEvent> = {}): TrackEvent {
  return {
    event_id: 1,
    track_id: 1,
    person_uuid: null,
    camera_id: 'CAM-N-001',
    object_class: 'person',
    first_seen: '2026-09-17T11:00:00+00:00',
    last_seen: '2026-09-17T11:01:00+00:00',
    total_time_seconds: 60,
    positions: [],
    alert_count: 0,
    max_risk_score: 50,
    zone_history: [],
    is_active: false,
    ...overrides,
  }
}

export function makeFrame(overrides: Partial<WSFrameUpdate> = {}): WSFrameUpdate {
  return {
    type: 'frame_update',
    camera_id: 'CAM-N-001',
    frame: '/9j/AAAA',
    detections: [],
    alerts: [],
    stats: { people_count: 1, vehicle_count: 0, animal_count: 0, active_alerts: 0, fps: 29.5 },
    timestamp: new Date().toISOString(),
    viewer_count: 2,
    server_timestamp: new Date().toISOString(),
    ...overrides,
  }
}

export function makeToken(overrides: Partial<TokenResponse> = {}): TokenResponse {
  return {
    access_token: 'access-token-value',
    refresh_token: 'refresh-token-value',
    token_type: 'bearer',
    expires_in: 900,
    user: { user_id: 'b1c2', username: 'admin', role: 'admin', camera_access: [], zone_access: [] },
    ...overrides,
  }
}

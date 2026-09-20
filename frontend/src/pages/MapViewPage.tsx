import type { LeafletMouseEvent, Map as LeafletMap } from 'leaflet'
import { Compass, Flame, LocateFixed, MapPinOff, Map as MapIcon, ShieldAlert } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { mergeAlerts } from '@/lib/alerts'
import { LocateCameraDialog } from '@/components/camera/LocateCameraDialog'
import { FocusedCameraPanel } from '@/components/map/FocusedCameraPanel'
import { IndiaMap } from '@/components/map/IndiaMap'
import { PanoramaView } from '@/components/map/PanoramaView'
import { BORDER_REGIONS, GEOFENCE_RINGS, type BorderRegion } from '@/lib/mapData'
import { MAP_STYLE_DEFINITIONS } from '@/components/map/mapStyles'
import { Badge, Button, Card, CardHeader, PageHeader, RiskBadge, Select, StatusDot, Switch } from '@/components/ui/primitives'
import { useAlerts } from '@/hooks/useAlerts'
import { useCameras } from '@/hooks/useData'
import { useKeyboard } from '@/hooks/useKeyboard'
import { INDIA_BOUNDS, RISK_LEVELS, RISK_META, ZONE_META } from '@/lib/constants'
import { formatRelative, titleCase } from '@/lib/utils'
import { usePermissions } from '@/stores/authStore'
import { useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'
import type { PanoramaLocation } from '@/lib/panorama'
import type { CameraWithAlertCount, RiskLevel } from '@/types'

export function MapViewPage() {
  const navigate = useNavigate()
  const cameras = useCameras()
  const [showAcknowledged, setShowAcknowledged] = useState(false)
  const [minimumLevel, setMinimumLevel] = useState<RiskLevel>('suspicious')
  const alerts = useAlerts({ limit: 500, ...(showAcknowledged ? {} : { acknowledged: false }) })
  const liveAlerts = useLiveStore((state) => state.liveAlerts)
  const mapStyle = useUiStore((state) => state.mapStyle)
  const cycleMapStyle = useUiStore((state) => state.cycleMapStyle)
  const [heatmap, setHeatmap] = useState(false)
  const { canManageCameras } = usePermissions()
  // /map?focus=CAM-N-001 (after locating a camera, or "View on map") centres on it with its fence rings.
  const [searchParams, setSearchParams] = useSearchParams()
  const focusId = searchParams.get('focus')?.toUpperCase() || null
  const [allFences, setAllFences] = useState(false)
  const [locating, setLocating] = useState<CameraWithAlertCount | null>(null)
  const [streetMode, setStreetMode] = useState(false)
  const [panorama, setPanorama] = useState<PanoramaLocation | null>(null)
  const mapRef = useRef<LeafletMap | null>(null)
  const [mapInstance, setMapInstance] = useState<LeafletMap | null>(null)
  const onMapReady = useCallback((map: LeafletMap | null) => {
    mapRef.current = map
    setMapInstance(map)
  }, [])

  // Street-view mode: the next click on the map is the point to look around from.
  useEffect(() => {
    if (!mapInstance || !streetMode) return undefined
    const container = mapInstance.getContainer()
    const previousCursor = container.style.cursor
    container.style.cursor = 'crosshair'
    const onClick = (event: LeafletMouseEvent) => setPanorama({ lat: Number(event.latlng.lat.toFixed(6)), lng: Number(event.latlng.lng.toFixed(6)) })
    mapInstance.on('click', onClick)
    return () => {
      mapInstance.off('click', onClick)
      container.style.cursor = previousCursor
    }
  }, [mapInstance, streetMode])

  const showRegion = (region: BorderRegion) => {
    mapRef.current?.flyToBounds(BORDER_REGIONS[region].bounds, { padding: [24, 24], duration: 0.8 })
    toast(BORDER_REGIONS[region].label, { id: 'map-region', duration: 1500 })
  }
  const toggleHeatmap = () => {
    setHeatmap((value) => {
      toast(`Threat heatmap ${value ? 'off' : 'on'}`, { id: 'heatmap', duration: 1200 })
      return !value
    })
  }

  const toggleFences = () => {
    setAllFences((value) => {
      toast(`Geo-fence rings ${value ? (focusId ? 'on the focused camera only' : 'off') : 'on every camera'}`, { id: 'fences', duration: 1400 })
      return !value
    })
  }
  const toggleStreetMode = () => {
    setStreetMode((value) => {
      if (!value) toast('Click anywhere on the map to look around from there', { id: 'street', duration: 2500, icon: '🧭' })
      return !value
    })
  }
  const clearFocus = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('focus')
    setSearchParams(next, { replace: true })
  }

  useKeyboard({
    v: toggleFences,
    y: toggleStreetMode,
    m: () => toast(`Map style: ${MAP_STYLE_DEFINITIONS[cycleMapStyle()].label}`, { id: 'map-style', duration: 1500 }),
    h: toggleHeatmap,
    g: () => mapRef.current?.flyToBounds(INDIA_BOUNDS, { padding: [12, 12], duration: 0.8 }),
    n: () => showRegion('north'),
    e: () => showRegion('east'),
    w: () => showRegion('west'),
    s: () => showRegion('south'),
    '+': () => mapRef.current?.zoomIn(),
    '=': () => mapRef.current?.zoomIn(),
    '-': () => mapRef.current?.zoomOut(),
  })

  const threshold = RISK_LEVELS.indexOf(minimumLevel)
  const visibleAlerts = useMemo(
    () =>
      mergeAlerts(alerts.data?.items ?? [], liveAlerts).filter(
        (alert) => RISK_LEVELS.indexOf(alert.risk_level) >= threshold && (showAcknowledged || !alert.acknowledged),
      ),
    [alerts.data, liveAlerts, threshold, showAcknowledged],
  )
  const cameraItems = cameras.data?.items ?? []
  const unplaced = cameraItems.filter((camera) => camera.gps_lat === null || camera.gps_lng === null)
  const focusedCamera = focusId ? cameraItems.find((camera) => camera.camera_id === focusId) : undefined
  const fences = allFences ? 'all' : focusedCamera ? 'focused' : 'off'
  const alertsWithoutGps = visibleAlerts.filter((alert) => alert.gps_lat === null || alert.gps_lng === null).length

  return (
    <div className="flex h-full flex-col gap-3 p-4" data-testid="map-view">
      <PageHeader
        icon={<MapIcon className="size-4" />}
        title="Threat Map"
        subtitle={`Style: ${MAP_STYLE_DEFINITIONS[mapStyle].label} · M styles · H heatmap · V fences · Y street view · G India · N/E/W/S regions · +/− zoom`}
        actions={
          <>
            <div className="flex items-center gap-1 rounded-lg border border-line bg-white p-1" role="group" aria-label="Border regions">
              {(['north', 'east', 'west', 'south'] as BorderRegion[]).map((region) => (
                <button
                  key={region}
                  type="button"
                  onClick={() => showRegion(region)}
                  title={BORDER_REGIONS[region].label}
                  className="rounded-md px-2 py-1 font-mono text-[11px] font-semibold text-slate-600 hover:bg-slate-100"
                >
                  {region.charAt(0).toUpperCase()}
                </button>
              ))}
              <button
                type="button"
                onClick={() => mapRef.current?.flyToBounds(INDIA_BOUNDS, { padding: [12, 12], duration: 0.8 })}
                title="India view (G)"
                className="rounded-md px-2 py-1 font-mono text-[11px] font-semibold text-slate-600 hover:bg-slate-100"
              >
                IN
              </button>
            </div>
            <Button size="sm" variant={heatmap ? 'primary' : 'secondary'} icon={<Flame className="size-3.5" />} onClick={toggleHeatmap} aria-pressed={heatmap} data-testid="heatmap-toggle">
              Heatmap <kbd className="kbd">H</kbd>
            </Button>
            <Button size="sm" variant={streetMode ? 'primary' : 'secondary'} icon={<Compass className="size-3.5" />} onClick={toggleStreetMode} aria-pressed={streetMode} data-testid="street-view-toggle">
              Street view <kbd className="kbd">Y</kbd>
            </Button>
            <Button size="sm" variant={allFences ? 'primary' : 'secondary'} icon={<ShieldAlert className="size-3.5" />} onClick={toggleFences} aria-pressed={allFences} data-testid="fences-toggle">
              Fences <kbd className="kbd">V</kbd>
            </Button>
            <Select value={minimumLevel} onChange={(event) => setMinimumLevel(event.target.value as RiskLevel)} className="h-8 w-44 text-xs" aria-label="Minimum risk level">
              {RISK_LEVELS.map((level) => (
                <option key={level} value={level}>
                  ≥ {RISK_META[level].label}
                </option>
              ))}
            </Select>
            <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
              <Switch checked={showAcknowledged} onCheckedChange={setShowAcknowledged} label="Show acknowledged alerts" /> Acknowledged
            </label>
          </>
        }
      />
      <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[1fr_300px]">
        <Card className="relative min-h-[520px] overflow-hidden">
          <IndiaMap
            cameras={cameraItems}
            alerts={visibleAlerts}
            className="h-full min-h-[520px]"
            heatmap={heatmap}
            focusCameraId={focusedCamera?.camera_id ?? null}
            fences={fences}
            onMapReady={onMapReady}
          />
          {focusedCamera ? (
            <FocusedCameraPanel
              camera={focusedCamera}
              onClose={clearFocus}
              onLocate={canManageCameras ? () => setLocating(focusedCamera) : undefined}
              onStreetView={
                focusedCamera.gps_lat !== null && focusedCamera.gps_lng !== null
                  ? () => setPanorama({ lat: focusedCamera.gps_lat ?? 0, lng: focusedCamera.gps_lng ?? 0, label: focusedCamera.camera_id })
                  : undefined
              }
            />
          ) : null}
        </Card>
        <div className="flex min-h-0 flex-col gap-3">
          {panorama ? <PanoramaView location={panorama} onClose={() => setPanorama(null)} className="h-80 shrink-0" /> : null}
          <Card>
            <CardHeader title="Legend" />
            <div className="space-y-1.5 p-3 text-xs">
              {RISK_LEVELS.map((level) => (
                <div key={level} className="flex items-center gap-2">
                  <span className="size-3 rounded-full border-2 border-white shadow" style={{ background: RISK_META[level].color }} />
                  {RISK_META[level].label}
                </div>
              ))}
              <div className="flex items-center gap-2 pt-1">
                <span className="size-3 rounded-sm border border-white bg-green-600 shadow" /> Camera online
              </div>
              <div className="flex items-center gap-2">
                <span className="size-3 rounded-sm border border-white bg-red-600 shadow" /> Camera offline
              </div>
              <div className="flex items-center gap-2">
                <span className="size-3 rounded-full border-2 border-red-600 bg-red-600/25" /> Critical alert on camera
              </div>
              {GEOFENCE_RINGS.map((ring) => (
                <div key={ring.zoneType} className="flex items-center gap-2">
                  <span className="size-3 rounded-full border-2" style={{ borderColor: ZONE_META[ring.zoneType].color }} /> {ZONE_META[ring.zoneType].label} fence · {ring.radius} m
                </div>
              ))}
            </div>
          </Card>
          <Card className="flex min-h-0 flex-1 flex-col">
            <CardHeader title={`Threats on map (${visibleAlerts.length - alertsWithoutGps})`} subtitle={alertsWithoutGps ? `${alertsWithoutGps} alert(s) without GPS` : undefined} />
            <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
              {visibleAlerts.slice(0, 100).map((alert) => (
                <button
                  type="button"
                  key={alert.alert_id}
                  onClick={() => navigate(`/alerts?alert=${encodeURIComponent(alert.alert_id)}`)}
                  className="flex w-full items-center justify-between gap-2 border-b border-slate-100 px-3 py-2 text-left text-xs hover:bg-slate-50"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-semibold">{titleCase(alert.alert_type)}</span>
                    <span className="block font-mono text-[10px] text-muted">
                      {alert.camera_id} · {formatRelative(alert.timestamp)}
                      {alert.gps_lat === null ? ' · no GPS' : ''}
                    </span>
                  </span>
                  <RiskBadge level={alert.risk_level} score={alert.risk_score} />
                </button>
              ))}
            </div>
          </Card>
          {unplaced.length ? (
            <Card>
              <CardHeader title="Cameras without GPS" icon={<MapPinOff className="size-4" />} subtitle="Not drawn on the map" />
              <div className="flex flex-col gap-1.5 p-3">
                {unplaced.map((camera) => (
                  <div key={camera.camera_id} className="flex items-center justify-between gap-2">
                    <button type="button" onClick={() => navigate(`/cameras/${camera.camera_id}`)}>
                      <Badge className="bg-white font-mono text-slate-700 ring-slate-200">
                        <StatusDot status={camera.status} /> {camera.camera_id}
                      </Badge>
                    </button>
                    {canManageCameras ? (
                      <Button size="xs" variant="hud" icon={<LocateFixed className="size-3.5" />} onClick={() => setLocating(camera)} data-testid={`locate-${camera.camera_id}`}>
                        Locate
                      </Button>
                    ) : null}
                  </div>
                ))}
              </div>
            </Card>
          ) : null}
        </div>
      </div>
      {locating ? <LocateCameraDialog camera={locating} onClose={() => setLocating(null)} /> : null}
    </div>
  )
}

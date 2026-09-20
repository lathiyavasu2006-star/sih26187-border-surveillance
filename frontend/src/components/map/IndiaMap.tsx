import type { Map as LeafletMap } from 'leaflet'
import { Layers } from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { MapContainer, Rectangle, ScaleControl, TileLayer, useMap } from 'react-leaflet'
import { GeoFences } from '@/components/map/GeoFences'
import { HeatLayer } from '@/components/map/HeatLayer'
import { CameraMarker, ThreatMarker } from '@/components/map/ThreatMarker'
import { MAP_STYLE_DEFINITIONS } from '@/components/map/mapStyles'
import { INDIA_BOUNDS, INDIA_CENTER } from '@/lib/constants'
import { CAMERA_FOCUS_ZOOM, heatPoints } from '@/lib/mapData'
import { cn } from '@/lib/utils'
import { MAP_STYLES, useUiStore } from '@/stores/uiStore'
import type { Alert, CameraWithAlertCount } from '@/types'

function FitBounds({ points, focus }: { points: [number, number][]; focus: [number, number] | null }) {
  const map = useMap()
  const key = points.map(([lat, lng]) => `${lat.toFixed(5)},${lng.toFixed(5)}`).join('|')
  const focusLat = focus?.[0] ?? null
  const focusLng = focus?.[1] ?? null
  useEffect(() => {
    // A focused camera wins over "show everything": fly to it close enough to see its fence rings.
    if (focusLat !== null && focusLng !== null) {
      map.flyTo([focusLat, focusLng], Math.min(CAMERA_FOCUS_ZOOM, map.getMaxZoom()), { duration: 0.9 })
      return
    }
    const positions: [number, number][] = key
      ? key.split('|').map((pair) => {
          const [lat, lng] = pair.split(',').map(Number)
          return [lat ?? 0, lng ?? 0]
        })
      : []
    if (positions.length === 0) map.fitBounds(INDIA_BOUNDS, { padding: [12, 12] })
    else if (positions.length === 1 && positions[0]) map.setView(positions[0], 11)
    else map.fitBounds(positions, { padding: [48, 48], maxZoom: 12 })
  }, [key, map, focusLat, focusLng])
  return null
}

function MapBridge({ onReady }: { onReady?: (map: LeafletMap | null) => void }) {
  const map = useMap()
  useEffect(() => {
    onReady?.(map)
    const container = map.getContainer()
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(container)
    return () => {
      observer.disconnect()
      onReady?.(null)
    }
  }, [map, onReady])
  return null
}

export function IndiaMap({
  cameras,
  alerts,
  className,
  showStylePicker = true,
  compact = false,
  interactive = true,
  heatmap = false,
  focusCameraId = null,
  fences = 'off',
  onMapReady,
}: {
  cameras: readonly CameraWithAlertCount[]
  alerts: readonly Alert[]
  className?: string
  showStylePicker?: boolean
  compact?: boolean
  interactive?: boolean
  heatmap?: boolean
  /** Centre on this camera (zoomed to its fence rings) instead of fitting every marker. */
  focusCameraId?: string | null
  /** Geo-fence rings: none, only around the focused camera, or around every placed camera. */
  fences?: 'off' | 'focused' | 'all'
  onMapReady?: (map: LeafletMap | null) => void
}) {
  const mapStyle = useUiStore((state) => state.mapStyle)
  const setMapStyle = useUiStore((state) => state.setMapStyle)
  const definition = MAP_STYLE_DEFINITIONS[mapStyle]

  const positions: [number, number][] = [
    ...cameras.flatMap((camera) => (camera.gps_lat !== null && camera.gps_lng !== null ? [[camera.gps_lat, camera.gps_lng] as [number, number]] : [])),
    ...alerts.flatMap((alert) => (alert.gps_lat !== null && alert.gps_lng !== null ? [[alert.gps_lat, alert.gps_lng] as [number, number]] : [])),
  ]
  const criticalCameras = useMemo(
    () => new Set(alerts.filter((alert) => !alert.acknowledged && alert.risk_level === 'critical').map((alert) => alert.camera_id)),
    [alerts],
  )
  const focused = focusCameraId ? cameras.find((camera) => camera.camera_id === focusCameraId) : undefined
  const focus: [number, number] | null = focused && focused.gps_lat !== null && focused.gps_lng !== null ? [focused.gps_lat, focused.gps_lng] : null
  const fencedCameras = fences === 'all' ? cameras : fences === 'focused' && focused ? [focused] : []
  const heat = useMemo(() => (heatmap ? heatPoints(alerts, cameras) : []), [heatmap, alerts, cameras])

  return (
    <div className={cn('relative overflow-hidden', className)} data-testid="india-map" data-map-style={mapStyle} data-heatmap={heatmap} data-fences={fences} data-focus={focus ? focusCameraId : undefined}>
      <MapContainer
        center={INDIA_CENTER}
        zoom={5}
        minZoom={4}
        maxBounds={[
          [INDIA_BOUNDS[0][0] - 8, INDIA_BOUNDS[0][1] - 12],
          [INDIA_BOUNDS[1][0] + 8, INDIA_BOUNDS[1][1] + 12],
        ]}
        className="h-full w-full"
        zoomControl={!compact && interactive}
        dragging={interactive}
        scrollWheelZoom={interactive}
        doubleClickZoom={interactive}
        touchZoom={interactive}
        boxZoom={interactive}
        keyboard={false}
        attributionControl
      >
        <TileLayer
          key={mapStyle}
          url={definition.url}
          attribution={definition.attribution}
          maxZoom={definition.maxZoom}
          {...(definition.subdomains ? { subdomains: definition.subdomains } : {})}
          {...(definition.className ? { className: definition.className } : {})}
        />
        <Rectangle bounds={INDIA_BOUNDS} pathOptions={{ color: definition.dark ? '#22d3ee' : '#0e7490', weight: 1, dashArray: '6 6', fill: false, opacity: 0.5 }} />
        {heat.length ? <HeatLayer points={heat} /> : null}
        {fencedCameras.length ? <GeoFences cameras={fencedCameras} /> : null}
        {cameras.map((camera) => (
          <CameraMarker key={camera.camera_id} camera={camera} critical={criticalCameras.has(camera.camera_id)} />
        ))}
        {alerts.map((alert) => (
          <ThreatMarker key={alert.alert_id} alert={alert} />
        ))}
        {!compact ? <ScaleControl position="bottomleft" imperial={false} /> : null}
        <FitBounds points={positions} focus={focus} />
        <MapBridge onReady={onMapReady} />
      </MapContainer>

      {showStylePicker ? (
        <div className="absolute right-3 top-3 z-[500] flex items-center gap-1 rounded-xl border border-line bg-white/95 p-1 shadow-lg backdrop-blur">
          <Layers className="mx-1 size-3.5 text-slate-500" aria-hidden />
          {MAP_STYLES.map((style) => (
            <button
              key={style}
              type="button"
              onClick={() => setMapStyle(style)}
              className={cn(
                'rounded-lg px-2 py-1 text-[11px] font-semibold',
                style === mapStyle ? 'bg-slate-900 text-hud' : 'text-slate-600 hover:bg-slate-100',
              )}
              aria-pressed={style === mapStyle}
            >
              {MAP_STYLE_DEFINITIONS[style].label.split(' ')[0]}
            </button>
          ))}
          <kbd className="kbd ml-1">M</kbd>
        </div>
      ) : null}
    </div>
  )
}

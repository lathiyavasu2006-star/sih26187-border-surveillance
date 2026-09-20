import L from 'leaflet'
import { Polygon, Polyline, Tooltip, useMapEvents, CircleMarker } from 'react-leaflet'
import { ZONE_META } from '@/lib/constants'
import type { Zone } from '@/types'

/** Zones that were drawn on the map, shown where the operator drew them. */
export function MapZones({ zones, onSelect }: { zones: readonly Zone[]; onSelect?: (zone: Zone) => void }) {
  return (
    <>
      {zones.map((zone) => {
        if (!zone.geo_polygon || zone.geo_polygon.length < 3) return null
        const colour = zone.color_hex || ZONE_META[zone.zone_type].color
        return (
          <Polygon
            key={`zone-${zone.zone_id}`}
            positions={zone.geo_polygon.map(([lat, lng]) => [lat, lng] as L.LatLngTuple)}
            pathOptions={{
              color: colour,
              weight: zone.is_active ? 2.5 : 1.5,
              dashArray: zone.is_active ? undefined : '6 6',
              fillColor: colour,
              fillOpacity: zone.is_active ? 0.22 : 0.08,
            }}
            eventHandlers={onSelect ? { click: () => onSelect(zone) } : undefined}
          >
            <Tooltip sticky>
              {zone.zone_name} · {ZONE_META[zone.zone_type].label} · {zone.camera_id}
              {zone.is_active ? '' : ' (inactive)'}
            </Tooltip>
          </Polygon>
        )
      })}
    </>
  )
}

/**
 * Collects the polygon the operator is drawing: every click adds a corner, and the shape is previewed as it
 * grows. The parent decides when it is finished.
 */
export function ZoneDrawing({ points, onAdd }: { points: readonly [number, number][]; onAdd: (point: [number, number]) => void }) {
  useMapEvents({
    click(event) {
      onAdd([Number(event.latlng.lat.toFixed(7)), Number(event.latlng.lng.toFixed(7))])
    },
  })

  if (!points.length) return null
  const positions: L.LatLngTuple[] = points.map(([lat, lng]) => [lat, lng])
  return (
    <>
      {points.length >= 3 ? (
        <Polygon positions={positions} pathOptions={{ color: '#22d3ee', weight: 2, dashArray: '6 4', fillColor: '#22d3ee', fillOpacity: 0.15 }} />
      ) : (
        <Polyline positions={positions} pathOptions={{ color: '#22d3ee', weight: 2, dashArray: '6 4' }} />
      )}
      {points.map((point, index) => (
        <CircleMarker
          key={`draw-${index}`}
          center={point}
          radius={5}
          pathOptions={{ color: '#0f172a', weight: 2, fillColor: '#22d3ee', fillOpacity: 1 }}
        >
          <Tooltip direction="top">Corner {index + 1}</Tooltip>
        </CircleMarker>
      ))}
    </>
  )
}

import L from 'leaflet'
import { useEffect, useMemo } from 'react'
import { MapContainer, Marker, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import { MAP_STYLE_DEFINITIONS } from '@/components/map/mapStyles'
import { INDIA_BOUNDS, INDIA_CENTER } from '@/lib/constants'

const pinIcon = L.divIcon({
  className: '',
  iconSize: [26, 34],
  iconAnchor: [13, 32],
  html: '<svg width="26" height="34" viewBox="0 0 26 34"><path d="M13 1C6.4 1 1 6.3 1 12.9 1 22 13 33 13 33s12-11 12-20.1C25 6.3 19.6 1 13 1z" fill="#0f172a" stroke="#22d3ee" stroke-width="2"/><circle cx="13" cy="13" r="4.5" fill="#22d3ee"/></svg>',
})

function ClickToPick({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(event) {
      onPick(Number(event.latlng.lat.toFixed(6)), Number(event.latlng.lng.toFixed(6)))
    },
  })
  return null
}

function CenterOn({ position }: { position: [number, number] | null }) {
  const map = useMap()
  useEffect(() => {
    if (position) map.setView(position, Math.max(map.getZoom(), 12))
    else map.fitBounds(INDIA_BOUNDS, { padding: [8, 8] })
    // Only when the picker opens; later picks must not move the view under the cursor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map])
  return null
}

/** Street map that reports the clicked coordinates (WGS84, 6 decimals ≈ 0.1 m). */
export function MapPicker({ lat, lng, onPick }: { lat: number | null; lng: number | null; onPick: (lat: number, lng: number) => void }) {
  const street = MAP_STYLE_DEFINITIONS.street
  const position = useMemo<[number, number] | null>(() => (lat !== null && lng !== null ? [lat, lng] : null), [lat, lng])
  return (
    <div className="overflow-hidden rounded-xl border border-line" data-testid="map-picker">
      <MapContainer center={position ?? INDIA_CENTER} zoom={position ? 12 : 5} minZoom={4} className="h-72 w-full cursor-crosshair" keyboard={false}>
        <TileLayer url={street.url} attribution={street.attribution} maxZoom={street.maxZoom} subdomains={street.subdomains ?? 'abc'} />
        {position ? <Marker position={position} icon={pinIcon} /> : null}
        <ClickToPick onPick={onPick} />
        <CenterOn position={position} />
      </MapContainer>
      <p className="border-t border-line bg-slate-50 px-3 py-1.5 text-[11px] text-muted">Click the map to place the camera. Zoom in (scroll) for street-level accuracy.</p>
    </div>
  )
}

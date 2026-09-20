import L from 'leaflet'
import { useEffect } from 'react'
import { useMap } from 'react-leaflet'

export type HeatPoint = [number, number, number]

interface HeatLayerOptions {
  radius?: number
  blur?: number
  maxZoom?: number
  minOpacity?: number
  gradient?: Record<number, string>
}

interface HeatLayer extends L.Layer {
  setLatLngs(points: HeatPoint[]): this
}

type HeatFactory = (points: HeatPoint[], options?: HeatLayerOptions) => HeatLayer

let factory: Promise<HeatFactory> | null = null

/** leaflet.heat is a classic plugin that extends the global `L`; expose the ES module instance first. */
function loadHeat(): Promise<HeatFactory> {
  if (!factory) {
    ;(globalThis as { L?: typeof L }).L = L
    factory = import('leaflet.heat').then(() => (L as unknown as { heatLayer: HeatFactory }).heatLayer)
  }
  return factory
}

/** Threat density layer: each point is [lat, lng, weight 0..1]. */
export function HeatLayer({ points }: { points: HeatPoint[] }) {
  const map = useMap()
  useEffect(() => {
    let layer: HeatLayer | null = null
    let cancelled = false
    void loadHeat().then((create) => {
      if (cancelled) return
      layer = create(points, {
        radius: 28,
        blur: 22,
        maxZoom: 12,
        minOpacity: 0.35,
        gradient: { 0.2: '#22d3ee', 0.45: '#eab308', 0.7: '#f97316', 1: '#dc2626' },
      })
      layer.addTo(map)
    })
    return () => {
      cancelled = true
      if (layer) map.removeLayer(layer)
    }
  }, [map, points])
  return null
}

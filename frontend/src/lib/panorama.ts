/**
 * Street-level panorama providers.
 *
 * Neither provider ships a usable key: Google Street View needs a Google Maps Platform key (billing account
 * required) and Mapillary needs a free access token. Both are read from the build environment, so the console
 * works without them and simply explains what to add.
 */
export type PanoramaProvider = 'google' | 'mapillary'

export interface PanoramaLocation {
  lat: number
  lng: number
  label?: string
}

const GOOGLE_KEY = (import.meta.env.VITE_GOOGLE_MAPS_KEY ?? '').trim()
const MAPILLARY_TOKEN = (import.meta.env.VITE_MAPILLARY_TOKEN ?? '').trim()

export const PANORAMA_PROVIDERS: Record<PanoramaProvider, { label: string; configured: boolean; setUp: string }> = {
  google: {
    label: 'Google Street View',
    configured: Boolean(GOOGLE_KEY),
    setUp: 'Add VITE_GOOGLE_MAPS_KEY to frontend/.env.local — a Google Maps Platform key with Maps JavaScript API enabled.',
  },
  mapillary: {
    label: 'Mapillary (open imagery)',
    configured: Boolean(MAPILLARY_TOKEN),
    setUp: 'Add VITE_MAPILLARY_TOKEN to frontend/.env.local — a free client token from mapillary.com (no card needed).',
  },
}

export function configuredProviders(): PanoramaProvider[] {
  return (Object.keys(PANORAMA_PROVIDERS) as PanoramaProvider[]).filter((name) => PANORAMA_PROVIDERS[name].configured)
}

/** Metres → degrees of latitude / longitude at this latitude, for a square search box. */
export function boundingBox(lat: number, lng: number, metres: number): [number, number, number, number] {
  const dLat = metres / 111_320
  const dLng = metres / (111_320 * Math.max(0.1, Math.cos((lat * Math.PI) / 180)))
  return [lng - dLng, lat - dLat, lng + dLng, lat + dLat]
}

export class PanoramaUnavailable extends Error {}

/** Id of the Mapillary image closest to the point, or null when nothing has been captured nearby. */
export async function nearestMapillaryImage(lat: number, lng: number, radiusMetres = 300): Promise<string | null> {
  const [west, south, east, north] = boundingBox(lat, lng, radiusMetres)
  const url = `https://graph.mapillary.com/images?access_token=${encodeURIComponent(MAPILLARY_TOKEN)}&fields=id,computed_geometry&bbox=${west},${south},${east},${north}&limit=25`
  const response = await fetch(url)
  if (!response.ok) {
    throw new PanoramaUnavailable(response.status === 401 ? 'The Mapillary token was rejected' : `Mapillary search failed (${response.status})`)
  }
  const payload = (await response.json()) as { data?: { id: string; computed_geometry?: { coordinates: [number, number] } }[] }
  const images = payload.data ?? []
  if (!images.length) return null
  // Closest first: the bbox search is not sorted by distance.
  const withDistance = images.map((image) => {
    const [imageLng, imageLat] = image.computed_geometry?.coordinates ?? [lng, lat]
    return { id: image.id, distance: (imageLat - lat) ** 2 + (imageLng - lng) ** 2 }
  })
  withDistance.sort((a, b) => a.distance - b.distance)
  return withDistance[0]?.id ?? null
}

export function mapillaryToken(): string {
  return MAPILLARY_TOKEN
}

export interface GoogleMapsApi {
  maps: {
    StreetViewService: new () => google.maps.StreetViewService
    StreetViewPanorama: new (container: HTMLElement, options?: google.maps.StreetViewPanoramaOptions) => google.maps.StreetViewPanorama
  }
}

function loadedGoogle(): GoogleMapsApi | undefined {
  return (window as Window).google as GoogleMapsApi | undefined
}

let googleLoader: Promise<GoogleMapsApi> | null = null

/** Loads the Google Maps JavaScript API once (Street View is only licensed through it). */
export function loadGoogleMaps(): Promise<GoogleMapsApi> {
  if (!GOOGLE_KEY) return Promise.reject(new PanoramaUnavailable(PANORAMA_PROVIDERS.google.setUp))
  const already = loadedGoogle()
  if (already?.maps?.StreetViewPanorama) return Promise.resolve(already)
  googleLoader ??= new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(GOOGLE_KEY)}&libraries=streetView&loading=async`
    script.async = true
    script.onerror = () => {
      googleLoader = null
      reject(new PanoramaUnavailable('Google Maps could not be loaded — check the API key and its restrictions'))
    }
    script.onload = () => {
      const api = loadedGoogle()
      if (api?.maps?.StreetViewPanorama) resolve(api)
      else reject(new PanoramaUnavailable('Google Maps loaded without the Street View library'))
    }
    document.head.appendChild(script)
  })
  return googleLoader
}

import { Compass, ExternalLink, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button, Spinner } from '@/components/ui/primitives'
import {
  PANORAMA_PROVIDERS,
  PanoramaUnavailable,
  configuredProviders,
  loadGoogleMaps,
  mapillaryToken,
  nearestMapillaryImage,
  type PanoramaLocation,
  type PanoramaProvider,
} from '@/lib/panorama'
import { cn } from '@/lib/utils'

type Status = { state: 'loading' } | { state: 'ready' } | { state: 'empty'; message: string } | { state: 'error'; message: string }

/** Google Street View at this point, or null when the nearest panorama is further than `radius` metres. */
async function mountGoogle(container: HTMLElement, location: PanoramaLocation, radius: number): Promise<boolean> {
  const maps = await loadGoogleMaps()
  const service = new maps.maps.StreetViewService()
  const data = await new Promise<google.maps.StreetViewPanoramaData | null>((resolve) => {
    service.getPanorama({ location: { lat: location.lat, lng: location.lng }, radius }, (result, status) =>
      resolve(status === 'OK' ? result : null),
    )
  })
  if (!data?.location?.pano) return false
  new maps.maps.StreetViewPanorama(container, {
    pano: data.location.pano,
    pov: { heading: 0, pitch: 0 },
    addressControl: true,
    motionTracking: false,
    motionTrackingControl: false,
    fullscreenControl: true,
  })
  return true
}

/** Mapillary viewer (open street-level imagery); returns false when nothing was captured nearby. */
async function mountMapillary(container: HTMLElement, location: PanoramaLocation, radius: number): Promise<{ ok: boolean; dispose?: () => void }> {
  const imageId = await nearestMapillaryImage(location.lat, location.lng, radius)
  if (!imageId) return { ok: false }
  const [{ Viewer }] = await Promise.all([import('mapillary-js'), import('mapillary-js/dist/mapillary.css')])
  const viewer = new Viewer({ accessToken: mapillaryToken(), container, imageId })
  return { ok: true, dispose: () => viewer.remove() }
}

/**
 * Street-level panorama for a point on the map: the operator can look around and walk along the road, the
 * way Google Street View does. Google Street View is used when a Maps key is configured, otherwise Mapillary's
 * open imagery. Without either key the panel explains exactly what to add.
 */
export function PanoramaView({ location, onClose, className }: { location: PanoramaLocation; onClose: () => void; className?: string }) {
  const available = configuredProviders()
  const [provider, setProvider] = useState<PanoramaProvider | null>(available[0] ?? null)
  const [radius, setRadius] = useState(300)
  const [status, setStatus] = useState<Status>({ state: 'loading' })
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!provider || !container) return undefined
    let cancelled = false
    let dispose: (() => void) | undefined
    setStatus({ state: 'loading' })
    container.replaceChildren()

    void (async () => {
      try {
        if (provider === 'google') {
          const found = await mountGoogle(container, location, radius)
          if (cancelled) return
          setStatus(found ? { state: 'ready' } : { state: 'empty', message: `No Street View imagery within ${radius} m of this point.` })
        } else {
          const result = await mountMapillary(container, location, radius)
          if (cancelled) {
            result.dispose?.()
            return
          }
          dispose = result.dispose
          setStatus(result.ok ? { state: 'ready' } : { state: 'empty', message: `No Mapillary imagery within ${radius} m of this point.` })
        }
      } catch (error) {
        if (cancelled) return
        setStatus({
          state: 'error',
          message: error instanceof PanoramaUnavailable || error instanceof Error ? error.message : 'The panorama could not be loaded',
        })
      }
    })()

    return () => {
      cancelled = true
      dispose?.()
      container.replaceChildren()
    }
  }, [provider, location, radius])

  return (
    <div className={cn('flex min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-slate-950', className)} data-testid="panorama-view">
      <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
        <p className="flex items-center gap-2 font-mono text-[11px] font-semibold text-hud">
          <Compass className="size-3.5" />
          STREET VIEW · {location.label ?? `${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}`}
        </p>
        <div className="flex items-center gap-1">
          {available.length > 1
            ? available.map((name) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => setProvider(name)}
                  className={cn('rounded-md px-2 py-1 text-[11px] font-semibold', provider === name ? 'bg-cyan-400/20 text-hud' : 'text-slate-400 hover:bg-slate-800')}
                  aria-pressed={provider === name}
                >
                  {PANORAMA_PROVIDERS[name].label.split(' ')[0]}
                </button>
              ))
            : null}
          <button type="button" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200" aria-label="Close street view">
            <X className="size-4" />
          </button>
        </div>
      </div>

      <div className="relative min-h-[320px] flex-1">
        <div ref={containerRef} className="absolute inset-0" />
        {provider === null ? (
          <div className="absolute inset-0 grid place-items-center p-6 text-center">
            <div className="max-w-md space-y-3">
              <Compass className="mx-auto size-8 text-slate-600" />
              <p className="text-sm font-semibold text-slate-200">Street-level imagery needs a provider key</p>
              <ul className="space-y-2 text-left text-[11px] text-slate-400">
                {(Object.keys(PANORAMA_PROVIDERS) as PanoramaProvider[]).map((name) => (
                  <li key={name} className="rounded-lg border border-slate-800 p-2">
                    <span className="block font-semibold text-slate-300">{PANORAMA_PROVIDERS[name].label}</span>
                    {PANORAMA_PROVIDERS[name].setUp}
                  </li>
                ))}
              </ul>
              <a
                href={`https://www.openstreetmap.org/?mlat=${location.lat}&mlon=${location.lng}#map=17/${location.lat}/${location.lng}`}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 text-[11px] font-semibold text-hud hover:underline"
              >
                Open this point in OpenStreetMap <ExternalLink className="size-3" />
              </a>
            </div>
          </div>
        ) : status.state === 'loading' ? (
          <div className="absolute inset-0 grid place-items-center bg-slate-950/80">
            <Spinner label="Looking for imagery near this point…" />
          </div>
        ) : status.state !== 'ready' ? (
          <div className="absolute inset-0 grid place-items-center p-6 text-center">
            <div className="space-y-3">
              <p className="text-xs text-slate-300" data-testid="panorama-message">
                {status.message}
              </p>
              {status.state === 'empty' && radius < 2000 ? (
                <Button size="xs" variant="hud" onClick={() => setRadius(radius * 3)}>
                  Search {radius * 3} m around the point
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

/**
 * Minimal declarations for the parts of the Google Maps JavaScript API the console uses (Street View only).
 * The full @types/google.maps package is not needed for this surface.
 */
declare namespace google.maps {
  interface LatLngLiteral {
    lat: number
    lng: number
  }

  interface StreetViewPov {
    heading: number
    pitch: number
  }

  interface StreetViewPanoramaOptions {
    pano?: string
    position?: LatLngLiteral
    pov?: StreetViewPov
    zoom?: number
    addressControl?: boolean
    fullscreenControl?: boolean
    motionTracking?: boolean
    motionTrackingControl?: boolean
    linksControl?: boolean
    panControl?: boolean
    zoomControl?: boolean
  }

  class StreetViewPanorama {
    constructor(container: HTMLElement, options?: StreetViewPanoramaOptions)
    setOptions(options: StreetViewPanoramaOptions): void
    setVisible(visible: boolean): void
  }

  interface StreetViewLocation {
    pano: string
    latLng?: { lat(): number; lng(): number }
  }

  interface StreetViewPanoramaData {
    location?: StreetViewLocation
  }

  type StreetViewStatus = 'OK' | 'ZERO_RESULTS' | 'UNKNOWN_ERROR'

  class StreetViewService {
    getPanorama(
      request: { location: LatLngLiteral; radius?: number; source?: string },
      callback: (data: StreetViewPanoramaData | null, status: StreetViewStatus) => void,
    ): void
  }
}

interface Window {
  google?: typeof google
}

import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PanoramaView } from '@/components/map/PanoramaView'
import { MAP_STYLE_DEFINITIONS } from '@/components/map/mapStyles'
import { PANORAMA_PROVIDERS, PanoramaUnavailable, boundingBox, configuredProviders, nearestMapillaryImage } from '@/lib/panorama'
import { MAP_STYLES } from '@/stores/uiStore'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('map styles', () => {
  it('offers the topographic and satellite-with-labels styles', () => {
    expect(MAP_STYLES).toContain('topographic')
    expect(MAP_STYLES).toContain('hybrid')
    expect(MAP_STYLE_DEFINITIONS.topographic.url).toContain('World_Topo_Map')
    expect(MAP_STYLE_DEFINITIONS.hybrid.url).toContain('World_Imagery')
  })

  it('gives every style a label, attribution and HTTPS tile URL', () => {
    for (const style of MAP_STYLES) {
      const definition = MAP_STYLE_DEFINITIONS[style]
      expect(definition.label.length).toBeGreaterThan(2)
      expect(definition.attribution).toBeTruthy()
      expect(definition.url.startsWith('https://')).toBe(true)
      expect(definition.maxZoom).toBeGreaterThanOrEqual(17)
    }
  })
})

describe('panorama search', () => {
  it('builds a square search box around the point', () => {
    const [west, south, east, north] = boundingBox(32.7266, 74.857, 300)
    expect(north - 32.7266).toBeCloseTo(0.0027, 4)
    expect(32.7266 - south).toBeCloseTo(0.0027, 4)
    expect(east).toBeGreaterThan(74.857)
    expect(west).toBeLessThan(74.857)
  })

  it('returns the image closest to the point', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          data: [
            { id: 'far', computed_geometry: { coordinates: [74.86, 32.73] } },
            { id: 'near', computed_geometry: { coordinates: [74.8571, 32.7267] } },
          ],
        }),
      }),
    )
    await expect(nearestMapillaryImage(32.7266, 74.857)).resolves.toBe('near')
    vi.unstubAllGlobals()
  })

  it('reports no imagery instead of failing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: [] }) }))
    await expect(nearestMapillaryImage(32.7266, 74.857)).resolves.toBeNull()
    vi.unstubAllGlobals()
  })

  it('explains a rejected token', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    await expect(nearestMapillaryImage(32.7266, 74.857)).rejects.toThrow(PanoramaUnavailable)
    vi.unstubAllGlobals()
  })
})

describe('PanoramaView', () => {
  it('explains how to enable street view when no provider key is configured', () => {
    expect(configuredProviders()).toEqual([]) // no keys in the test environment
    render(<PanoramaView location={{ lat: 32.7266, lng: 74.857, label: 'CAM-N-001' }} onClose={() => undefined} />)
    expect(screen.getByTestId('panorama-view')).toHaveTextContent('CAM-N-001')
    expect(screen.getByText(/Street-level imagery needs a provider key/)).toBeInTheDocument()
    expect(screen.getByText(new RegExp(PANORAMA_PROVIDERS.mapillary.setUp.slice(0, 30)))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /OpenStreetMap/ })).toHaveAttribute('href', expect.stringContaining('32.7266'))
  })
})

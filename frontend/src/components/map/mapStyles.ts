import type { MapStyle } from '@/stores/uiStore'

export interface MapStyleDefinition {
  label: string
  url: string
  attribution: string
  subdomains?: string
  maxZoom: number
  /** Extra class on the tile layer (used for the night-vision tactical filter). */
  className?: string
  dark: boolean
}

/** Transparent label layer drawn over satellite imagery for the Hybrid style. */
export const HYBRID_OVERLAY = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Labels &copy; Esri',
  maxZoom: 19,
}

export const MAP_STYLE_DEFINITIONS: Record<MapStyle, MapStyleDefinition> = {
  standard: {
    label: 'Standard',
    url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 20,
    dark: false,
  },
  street: {
    label: 'Street',
    // Full OpenStreetMap rendering: every road and street is drawn from zoom 12 upward.
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    subdomains: 'abc',
    maxZoom: 19,
    dark: false,
  },
  topographic: {
    label: 'Topographic',
    // Relief shading with roads and place names — the terrain reading used for border sector planning.
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri — Esri, DeLorme, NAVTEQ, TomTom, Intermap, USGS, iPC',
    maxZoom: 19,
    dark: false,
  },
  hybrid: {
    label: 'Hybrid',
    // Satellite imagery with the road and boundary labels drawn on top (see HYBRID_OVERLAY).
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics',
    maxZoom: 19,
    dark: true,
  },
  light: {
    label: 'Light',
    url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 20,
    dark: false,
  },
  dark: {
    label: 'Dark',
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 20,
    dark: true,
  },
  satellite: {
    label: 'Satellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19,
    dark: true,
  },
  terrain: {
    label: 'Terrain',
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors, SRTM &copy; OpenTopoMap (CC-BY-SA)',
    subdomains: 'abc',
    maxZoom: 17,
    dark: false,
  },
  tactical: {
    label: 'Tactical (night vision)',
    url: 'https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 20,
    className: 'tactical-tiles',
    dark: true,
  },
}

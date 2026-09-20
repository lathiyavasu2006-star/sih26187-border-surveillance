import {
  Activity,
  Bell,
  Cctv,
  Cpu,
  FileLock2,
  Hexagon,
  LayoutDashboard,
  Map as MapIcon,
  Route,
  ScanSearch,
  Settings,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  path: string
  title: string
  icon: LucideIcon
  shortcut: string
  description: string
}

export const NAV_ITEMS: NavItem[] = [
  { path: '/dashboard', title: 'Dashboard', icon: LayoutDashboard, shortcut: 'alt+1', description: 'Command overview' },
  { path: '/cameras', title: 'Live Cameras', icon: Cctv, shortcut: 'alt+2', description: 'Live HUD feeds' },
  { path: '/map', title: 'Map', icon: MapIcon, shortcut: 'alt+3', description: 'India threat map' },
  { path: '/alerts', title: 'Alerts', icon: Bell, shortcut: 'alt+4', description: 'Alert queue' },
  { path: '/events', title: 'Events', icon: Route, shortcut: 'alt+5', description: 'Tracks & timelines' },
  { path: '/evidence', title: 'Evidence', icon: FileLock2, shortcut: 'alt+6', description: 'Chain of custody' },
  { path: '/analysis', title: 'Video Analysis', icon: ScanSearch, shortcut: 'alt+v', description: 'Analyze any video file' },
  { path: '/zones', title: 'Zone Manager', icon: Hexagon, shortcut: 'alt+7', description: 'Fence zones' },
  { path: '/hardware', title: 'Hardware', icon: Cpu, shortcut: 'alt+8', description: 'Sensors & devices' },
  { path: '/analytics', title: 'Analytics', icon: Activity, shortcut: 'alt+9', description: 'Trends & health' },
  { path: '/settings', title: 'Settings', icon: Settings, shortcut: 'alt+0', description: 'Account & console' },
]

/** Tab title for any application path (camera detail tabs are named after the camera). */
export function titleForPath(pathname: string): string {
  const camera = /^\/cameras\/([^/]+)$/.exec(pathname)
  if (camera?.[1]) return decodeURIComponent(camera[1])
  const item = NAV_ITEMS.find((nav) => nav.path === pathname)
  return item?.title ?? 'Console'
}

export function iconForPath(pathname: string): LucideIcon {
  if (pathname.startsWith('/cameras')) return Cctv
  return NAV_ITEMS.find((nav) => nav.path === pathname)?.icon ?? LayoutDashboard
}

import type { Alert } from '@/types'

/** Merges REST alerts with ones that arrived over the WebSocket since the last fetch (newest first). */
export function mergeAlerts(rest: readonly Alert[], live: readonly Alert[]): Alert[] {
  const byId = new Map<string, Alert>()
  for (const alert of live) byId.set(alert.alert_id, alert)
  for (const alert of rest) byId.set(alert.alert_id, alert) // REST copy is authoritative (acknowledgement state)
  return [...byId.values()].sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
}

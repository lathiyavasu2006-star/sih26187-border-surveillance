import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'
import toast from 'react-hot-toast'
import { alertsApi } from '@/api/endpoints'
import { POLL } from '@/lib/constants'
import { cameraSockets } from '@/lib/cameraSocket'
import { useAuthStore } from '@/stores/authStore'
import { useLiveStore } from '@/stores/liveStore'
import type { Alert, AlertFilters, AlertListResponse } from '@/types'

export const alertKeys = {
  all: ['alerts'] as const,
  list: (filters: AlertFilters) => ['alerts', 'list', filters] as const,
  stats: ['alerts', 'stats'] as const,
  detail: (alertId: string) => ['alerts', 'detail', alertId] as const,
}

/** Minimum spacing between WebSocket-triggered refetches (protects the per-client rate limit). */
const INVALIDATE_THROTTLE_MS = 5_000

/**
 * Invalidates alert queries when the live feed reports new or acknowledged alerts. Mounted once in the
 * application shell so every alert view stays fresh without polling faster.
 */
export function useAlertLiveSync(): void {
  const queryClient = useQueryClient()
  const lastRun = useRef(0)
  const pending = useRef<number | null>(null)

  useEffect(() => {
    const invalidate = () => {
      const wait = INVALIDATE_THROTTLE_MS - (Date.now() - lastRun.current)
      if (wait <= 0) {
        lastRun.current = Date.now()
        void queryClient.invalidateQueries({ queryKey: alertKeys.all })
        void queryClient.invalidateQueries({ queryKey: ['stats'] })
        return
      }
      if (pending.current === null) {
        pending.current = window.setTimeout(() => {
          pending.current = null
          lastRun.current = Date.now()
          void queryClient.invalidateQueries({ queryKey: alertKeys.all })
          void queryClient.invalidateQueries({ queryKey: ['stats'] })
        }, wait)
      }
    }
    const unsubscribe = cameraSockets.onEvent((event) => {
      if (event.type === 'alerts' || event.type === 'alert_acknowledged') invalidate()
      if (event.type === 'notification' && event.notification.kind === 'critical_alert') invalidate()
      if (event.type === 'camera_status') void queryClient.invalidateQueries({ queryKey: ['cameras'] })
    })
    return () => {
      unsubscribe()
      if (pending.current !== null) window.clearTimeout(pending.current)
    }
  }, [queryClient])
}

export function useAlerts(filters: AlertFilters = {}, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: alertKeys.list(filters),
    queryFn: () => alertsApi.list(filters),
    refetchInterval: POLL.alerts,
    placeholderData: (previous) => previous,
    enabled: options.enabled ?? true,
  })
}

export function useAlertStats() {
  return useQuery({ queryKey: alertKeys.stats, queryFn: alertsApi.stats, refetchInterval: POLL.alertStats })
}

export function useAlert(alertId: string | null) {
  return useQuery({
    queryKey: alertKeys.detail(alertId ?? ''),
    queryFn: () => alertsApi.get(alertId ?? ''),
    enabled: Boolean(alertId),
  })
}

export interface AcknowledgeInput {
  alertId: string
  falseAlarm: boolean
  notes?: string
}

export function useAcknowledgeAlert() {
  const queryClient = useQueryClient()
  const userId = useAuthStore((state) => state.user?.user_id)
  return useMutation({
    mutationFn: ({ alertId, falseAlarm, notes }: AcknowledgeInput) => {
      if (!userId) throw new Error('Not signed in')
      if (falseAlarm && !notes?.trim()) throw new Error('A note is required when marking a false alarm')
      return alertsApi.acknowledge(alertId, {
        acknowledged_by: userId,
        false_alarm: falseAlarm,
        notes: notes?.trim() ? notes.trim() : null,
      })
    },
    onSuccess: (alert: Alert) => {
      useLiveStore.getState().markAlertAcknowledged(alert.alert_id, alert.false_alarm)
      queryClient.setQueryData(alertKeys.detail(alert.alert_id), alert)
      queryClient.setQueriesData<AlertListResponse>({ queryKey: ['alerts', 'list'] }, (previous) =>
        previous
          ? {
              ...previous,
              items: previous.items.map((item) => (item.alert_id === alert.alert_id ? alert : item)),
              unacknowledged_count: Math.max(
                0,
                previous.unacknowledged_count -
                  (previous.items.some((item) => item.alert_id === alert.alert_id && !item.acknowledged) ? 1 : 0),
              ),
            }
          : previous,
      )
      void queryClient.invalidateQueries({ queryKey: alertKeys.stats })
      toast.success(alert.false_alarm ? `${alert.alert_id} marked as false alarm` : `${alert.alert_id} acknowledged`)
    },
    onError: (error: Error) => toast.error(error.message),
  })
}

/** Unacknowledged alert count: REST total, kept current by live sync. */
export function useUnacknowledgedCount(): number {
  const { data } = useAlerts({ acknowledged: false, limit: 1 })
  return data?.unacknowledged_count ?? 0
}

export function useAlertSelection() {
  const queryClient = useQueryClient()
  return useCallback(
    (alertId: string) => queryClient.getQueryData<Alert>(alertKeys.detail(alertId)) ?? null,
    [queryClient],
  )
}

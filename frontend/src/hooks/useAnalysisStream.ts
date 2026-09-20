import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { refreshAccessToken } from '@/api/client'
import { evidenceApi } from '@/api/endpoints'
import { isRecord } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import type { AnalysisJob } from '@/types'

/** Polling fallback while the live socket is not connected (30 requests/min at most). */
const FALLBACK_POLL_MS = 2_000

function wsOrigin(): string {
  const configured = (import.meta.env.VITE_WS_URL || '').trim()
  if (configured) return configured.replace(/\/+$/, '')
  return `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
}

export interface AnalysisStreamState {
  job: AnalysisJob | null
  /** Latest annotated frame (base64 JPEG) streamed by the worker. */
  frame: string | null
  live: boolean
}

function isFinished(job: AnalysisJob | null | undefined): boolean {
  return job?.status === 'complete' || job?.status === 'failed'
}

/**
 * Live progress and annotated frames of an analysis job over /ws/analysis/{job_id} (not counted against the
 * HTTP rate limit). Falls back to polling the REST status when the socket is unavailable.
 */
export function useAnalysisStream(jobId: string | null, initial: AnalysisJob | null = null): AnalysisStreamState {
  const [streamed, setStreamed] = useState<{ jobId: string; job: AnalysisJob | null; frame: string | null; live: boolean } | null>(null)
  const current = streamed?.jobId === jobId ? streamed : null

  useEffect(() => {
    if (!jobId) return undefined
    let socket: WebSocket | null = null
    let closed = false
    let finished = false

    const connect = async () => {
      let token = useAuthStore.getState().accessToken
      const expiresAt = useAuthStore.getState().accessExpiresAt
      if (!token || (expiresAt !== null && expiresAt - Date.now() < 30_000)) token = await refreshAccessToken()
      if (!token || closed) return
      socket = new WebSocket(`${wsOrigin()}/ws/analysis/${encodeURIComponent(jobId)}?token=${encodeURIComponent(token)}`)
      socket.onopen = () => setStreamed((previous) => ({ jobId, job: previous?.jobId === jobId ? previous.job : null, frame: previous?.jobId === jobId ? previous.frame : null, live: true }))
      socket.onmessage = (event: MessageEvent) => {
        let message: unknown
        try {
          message = JSON.parse(String(event.data))
        } catch {
          return
        }
        if (!isRecord(message) || typeof message.type !== 'string') return
        if (message.type === 'analysis_frame' && typeof message.frame === 'string') {
          const frame = message.frame
          setStreamed((previous) => ({ jobId, job: previous?.jobId === jobId ? previous.job : null, frame, live: true }))
        } else if ((message.type === 'analysis_progress' || message.type === 'analysis_complete') && isRecord(message.job)) {
          const job = message.job as unknown as AnalysisJob
          if (message.type === 'analysis_complete') finished = true
          setStreamed((previous) => ({ jobId, job, frame: previous?.jobId === jobId ? previous.frame : null, live: !finished }))
        }
      }
      socket.onclose = () => {
        setStreamed((previous) => (previous && previous.jobId === jobId ? { ...previous, live: false } : previous))
      }
    }
    void connect()
    return () => {
      closed = true
      socket?.close()
    }
  }, [jobId])

  // REST fallback: polls only while the socket is not delivering and the job is still running.
  const streamedJob = current?.job ?? null
  const needsPolling = Boolean(jobId) && !current?.live && !isFinished(streamedJob)
  const polled = useQuery({
    queryKey: ['evidence', 'analysis-job', jobId],
    queryFn: () => evidenceApi.analysisJob(jobId ?? ''),
    enabled: needsPolling,
    refetchInterval: (query) => (isFinished(query.state.data) ? false : FALLBACK_POLL_MS),
  })

  const candidates = [streamedJob, polled.data ?? null, initial].filter((job): job is AnalysisJob => job !== null && job.job_id === jobId)
  // Prefer a finished record, otherwise the most advanced one.
  const job = candidates.find(isFinished) ?? candidates.sort((a, b) => b.percent - a.percent)[0] ?? null
  return { job, frame: current?.frame ?? null, live: Boolean(current?.live) }
}

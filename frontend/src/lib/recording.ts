import { evidenceApi } from '@/api/endpoints'
import { useLiveStore } from '@/stores/liveStore'
import type { EvidenceUploadResponse } from '@/types'

/** The evidence endpoint accepts MP4/AVI/MOV/MKV; Chrome/Edge can record MP4 (H.264) natively. */
export const RECORDING_MIME_TYPES = ['video/mp4;codecs=avc1.42E01E', 'video/mp4;codecs=avc1', 'video/mp4']
export const MAX_RECORDING_MS = 5 * 60_000

function stamp(date: Date = new Date()): string {
  return date.toISOString().replace(/[-:]/g, '').replace(/\..+$/, '').replace('T', '_')
}

export function base64ToBytes(base64: string): Uint8Array<ArrayBuffer> {
  const binary = atob(base64)
  const bytes = new Uint8Array(new ArrayBuffer(binary.length))
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes
}

/**
 * Saves the camera's current frame (exactly as received from the ML pipeline) as manual-snapshot evidence.
 * The backend hashes it (SHA-256) while it is written.
 */
export async function saveLiveSnapshot(cameraId: string): Promise<EvidenceUploadResponse> {
  const live = useLiveStore.getState().cameras[cameraId]
  if (!live?.frame || Date.now() - live.frameReceivedAt > 5_000) throw new Error('No live frame to capture')
  const file = new File([base64ToBytes(live.frame)], `${cameraId}_snapshot_${stamp()}.jpg`, { type: 'image/jpeg' })
  return evidenceApi.upload(file, { camera_id: cameraId })
}

export function supportedRecordingType(): string | null {
  if (typeof MediaRecorder === 'undefined') return null
  return RECORDING_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? null
}

export interface ActiveRecording {
  startedAt: number
  /** Stops the recorder and uploads the clip; resolves with the stored evidence. */
  stop: () => Promise<EvidenceUploadResponse>
}

/** Records the operator's view of a camera canvas (frame + HUD) at 15 fps. */
export function startCanvasRecording(canvas: HTMLCanvasElement, cameraId: string, onAutoStop?: (result: Promise<EvidenceUploadResponse>) => void): ActiveRecording {
  const mimeType = supportedRecordingType()
  if (!mimeType) throw new Error('This browser cannot record MP4 video; use Chrome or Edge')
  const stream = canvas.captureStream(15)
  const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 2_500_000 })
  const chunks: Blob[] = []
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data)
  }
  const startedAt = Date.now()
  recorder.start(1_000)

  let stopping: Promise<EvidenceUploadResponse> | null = null
  const stop = () => {
    if (stopping) return stopping
    stopping = new Promise<Blob>((resolve) => {
      recorder.onstop = () => resolve(new Blob(chunks, { type: 'video/mp4' }))
      if (recorder.state !== 'inactive') recorder.stop()
      else resolve(new Blob(chunks, { type: 'video/mp4' }))
    }).then((blob) => {
      stream.getTracks().forEach((track) => track.stop())
      if (blob.size === 0) throw new Error('Nothing was recorded')
      const file = new File([blob], `${cameraId}_recording_${stamp(new Date(startedAt))}.mp4`, { type: 'video/mp4' })
      return evidenceApi.upload(file, { camera_id: cameraId })
    })
    return stopping
  }
  const timer = window.setTimeout(() => onAutoStop?.(stop()), MAX_RECORDING_MS)
  return {
    startedAt,
    stop: () => {
      window.clearTimeout(timer)
      return stop()
    },
  }
}

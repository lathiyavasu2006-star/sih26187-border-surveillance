import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FileVideo, History, ScanSearch, UploadCloud } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { evidenceApi } from '@/api/endpoints'
import { JobProgress, JobSummary, LiveAnalysisView } from '@/components/analysis/AnalysisViews'
import { Button, Card, CardHeader, Field, PageHeader, ProgressBar, Select } from '@/components/ui/primitives'
import { useAnalysisStream } from '@/hooks/useAnalysisStream'
import { useCameras } from '@/hooks/useData'
import { cn, formatBytes, formatTime } from '@/lib/utils'
import { validateAnalysisFile } from '@/lib/validation'
import { usePermissions } from '@/stores/authStore'
import type { AnalysisJob } from '@/types'

const NO_CAMERA = '__standalone__'
const VIDEO_ACCEPT = '.mp4,.avi,.mov,.mkv'

interface RunRecord {
  job: AnalysisJob
  fileName: string
  cameraId: string | null
  startedAt: number
}

/**
 * Analyse any video file — no camera registration needed. The file is uploaded, YOLOv8x + tracking + the
 * risk engine run over it in a worker, and the annotated frames stream back live while it is processed.
 * With a camera selected the video is stored as evidence first and high-risk findings become alerts.
 */
export function VideoAnalysisPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const cameras = useCameras()
  const { canUploadEvidence } = usePermissions()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [target, setTarget] = useState(NO_CAMERA)
  const [dragging, setDragging] = useState(false)
  const [uploadPercent, setUploadPercent] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [activeJob, setActiveJob] = useState<AnalysisJob | null>(null)
  const [runs, setRuns] = useState<RunRecord[]>([])
  const stream = useAnalysisStream(activeJob?.job_id ?? null, activeJob)
  const job = stream.job

  const start = useMutation({
    mutationFn: async (): Promise<AnalysisJob> => {
      const problem = validateAnalysisFile(file)
      if (problem || !file) throw new Error(problem ?? 'Choose a video file')
      setUploadPercent(0)
      if (target === NO_CAMERA) return evidenceApi.analyzeStandalone(file, setUploadPercent)
      const uploaded = await evidenceApi.upload(file, { camera_id: target }, setUploadPercent)
      void queryClient.invalidateQueries({ queryKey: ['evidence'] })
      return evidenceApi.analyze(uploaded.evidence_id)
    },
    onSuccess: (created) => {
      setActiveJob(created)
      setRuns((previous) => [{ job: created, fileName: file?.name ?? 'video', cameraId: created.camera_id, startedAt: Date.now() }, ...previous].slice(0, 10))
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const completedJob = job?.status === 'complete' ? job.job_id : null
  useEffect(() => {
    if (!completedJob) return
    void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    void queryClient.invalidateQueries({ queryKey: ['evidence', 'list'] })
  }, [completedJob, queryClient])

  const running = start.isPending || (job !== null && (job.status === 'queued' || job.status === 'loading' || job.status === 'running'))

  const choose = (picked: File | null) => {
    setFile(picked)
    setError(picked ? validateAnalysisFile(picked) : null)
  }
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (!running) choose(event.dataTransfer.files[0] ?? null)
  }
  const begin = () => {
    setError(null)
    const problem = validateAnalysisFile(file)
    if (problem) {
      setError(problem)
      return
    }
    start.mutate()
  }

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="video-analysis-page">
      <PageHeader
        icon={<ScanSearch className="size-4" />}
        title="Video Analysis"
        subtitle="Drop any video — no camera registration needed — and watch YOLOv8x detections, tracking and risk scoring live"
      />
      <div className="grid gap-4 xl:grid-cols-[380px_1fr]">
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader title="Analyze a video" icon={<UploadCloud className="size-4" />} subtitle="MP4 · AVI · MOV · MKV up to 500 MB" />
            <div className="flex flex-col gap-4 p-4">
              <div
                role="button"
                tabIndex={0}
                onClick={() => !running && inputRef.current?.click()}
                onKeyDown={(event) => {
                  if ((event.key === 'Enter' || event.key === ' ') && !running) inputRef.current?.click()
                }}
                onDragOver={(event) => {
                  event.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                className={cn(
                  'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors',
                  dragging ? 'border-cyan-500 bg-cyan-50' : 'border-line bg-slate-50 hover:border-cyan-400',
                  running && 'cursor-not-allowed opacity-60',
                )}
                data-testid="analysis-dropzone"
              >
                <FileVideo className="size-8 text-slate-400" />
                {file ? (
                  <>
                    <p className="max-w-full truncate text-sm font-semibold text-slate-900">{file.name}</p>
                    <p className="text-xs text-muted">{formatBytes(file.size)} · click to change</p>
                  </>
                ) : (
                  <>
                    <p className="text-sm font-semibold text-slate-800">Drop a video here or click to browse</p>
                    <p className="text-xs text-muted">Footage from a phone, drone, CCTV export — any source</p>
                  </>
                )}
                <input
                  ref={inputRef}
                  type="file"
                  accept={VIDEO_ACCEPT}
                  className="hidden"
                  onChange={(event) => choose(event.target.files?.[0] ?? null)}
                  data-testid="analysis-file"
                />
              </div>

              <Field
                label="Camera (optional)"
                htmlFor="analysis-camera"
                hint={
                  target === NO_CAMERA
                    ? 'Results only: no zones, no alerts, nothing stored; the upload is deleted afterwards.'
                    : 'Stored as evidence (SHA-256); the camera’s zones apply and high-risk findings become alerts.'
                }
              >
                <Select id="analysis-camera" value={target} disabled={running} onChange={(event) => setTarget(event.target.value)}>
                  <option value={NO_CAMERA}>No camera — just analyze this video</option>
                  {(cameras.data?.items ?? []).map((camera) => (
                    <option key={camera.camera_id} value={camera.camera_id}>
                      {camera.camera_id} · {camera.name}
                    </option>
                  ))}
                </Select>
              </Field>

              {start.isPending ? (
                <div className="flex flex-col gap-1.5">
                  <p className="text-xs font-semibold text-slate-700">Uploading… {uploadPercent}%</p>
                  <ProgressBar value={uploadPercent} className="h-2" />
                </div>
              ) : null}
              {error ? (
                <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
                  {error}
                </p>
              ) : null}
              <Button
                variant="primary"
                size="md"
                icon={<ScanSearch className="size-4" />}
                loading={running}
                disabled={running || !file || !canUploadEvidence}
                onClick={begin}
                data-testid="analysis-start"
              >
                {running ? 'Analysing…' : 'Start Analysis'}
              </Button>
              {!canUploadEvidence ? <p className="text-[11px] text-muted">Video analysis requires the supervisor role or above.</p> : null}
            </div>
          </Card>

          {runs.length ? (
            <Card>
              <CardHeader title="This session" icon={<History className="size-4" />} />
              <ul>
                {runs.map((run) => (
                  <li key={run.job.job_id}>
                    <button
                      type="button"
                      onClick={() => setActiveJob(run.job)}
                      className={cn('flex w-full items-center justify-between gap-2 border-t border-slate-100 px-4 py-2 text-left text-xs hover:bg-slate-50', activeJob?.job_id === run.job.job_id && 'bg-cyan-50')}
                    >
                      <span className="min-w-0 truncate font-medium text-slate-800">{run.fileName}</span>
                      <span className="shrink-0 text-muted">
                        {run.cameraId ?? 'no camera'} · {formatTime(run.startedAt).slice(0, 5)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </div>

        <div className="flex flex-col gap-4">
          <LiveAnalysisView job={job} frame={stream.frame} live={stream.live} />
          {job ? (
            <Card className="p-4">
              <JobProgress job={job} />
            </Card>
          ) : null}
          {job?.status === 'complete' ? (
            <>
              <JobSummary job={job} />
              <div className="flex flex-wrap justify-end gap-2">
                <Button
                  onClick={() => {
                    setFile(null)
                    setActiveJob(null)
                    start.reset()
                  }}
                >
                  Analyze another
                </Button>
                {!job.standalone ? (
                  <>
                    <Button onClick={() => navigate('/evidence')}>View Evidence</Button>
                    <Button variant="primary" onClick={() => navigate('/alerts')}>
                      View Alerts
                    </Button>
                  </>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

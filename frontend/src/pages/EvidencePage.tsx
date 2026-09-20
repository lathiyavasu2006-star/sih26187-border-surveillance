import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ChevronLeft, ChevronRight, Download, FileLock2, FileVideo, Play, ScanSearch, ShieldCheck, ShieldX, Trash2, Upload } from 'lucide-react'
import { useMemo, useRef, useState, type FormEvent } from 'react'
import toast from 'react-hot-toast'
import { evidenceRequestPath, fetchProtectedObjectUrl } from '@/api/client'
import { evidenceApi } from '@/api/endpoints'
import { AnalyzeVideoDialog } from '@/components/evidence/AnalyzeVideoDialog'
import { ProtectedImage } from '@/components/ui/ProtectedImage'
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, Field, Input, Modal, PageHeader, Select, Spinner } from '@/components/ui/primitives'
import { useCameras, useProtectedFile } from '@/hooks/useData'
import { cn, formatBytes, formatDateTime, formatDuration, shortHash, titleCase } from '@/lib/utils'
import { usePermissions } from '@/stores/authStore'
import type { Evidence, EvidenceFilters, EvidenceType, EvidenceVerifyResponse } from '@/types'

const PAGE_SIZE = 24
const VIDEO_TYPES: EvidenceType[] = ['video_clip', 'uploaded_video']
const ALLOWED_UPLOAD = '.mp4,.avi,.mov,.mkv,.jpg,.jpeg,.png'
const MAX_UPLOAD_BYTES = 500 * 1024 * 1024

/** mm:ss (or h:mm:ss) for video durations. */
function clock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  const whole = Math.max(0, Math.round(seconds))
  const h = Math.floor(whole / 3600)
  const m = Math.floor((whole % 3600) / 60)
  const sec = String(whole % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

function EvidenceViewer({ evidence }: { evidence: Evidence }) {
  const isVideo = VIDEO_TYPES.includes(evidence.evidence_type)
  const path = evidenceRequestPath(evidence.file_url)
  // Videos come from /playback: the original when the browser can decode it, otherwise an H.264 preview
  // derived on the server (the evidence file and its SHA-256 are untouched).
  const video = useProtectedFile(isVideo && evidence.file_exists ? evidenceApi.playbackPath(evidence.evidence_id) : null)
  const [playedDuration, setPlayedDuration] = useState<number | null>(null)
  if (!evidence.file_exists) {
    return <div className="flex aspect-video items-center justify-center rounded-xl bg-slate-900 text-xs text-red-300">File missing from storage</div>
  }
  if (isVideo) {
    return video.url ? (
      <div className="flex flex-col gap-1">
        <video
          src={video.url}
          controls
          playsInline
          preload="metadata"
          onLoadedMetadata={(event) => setPlayedDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : null)}
          className="aspect-video w-full rounded-xl bg-black"
          data-testid="evidence-video"
        />
        <p className="text-right font-mono text-[11px] text-muted" data-testid="evidence-video-duration">
          Duration {clock(playedDuration ?? evidence.duration_seconds)}
        </p>
      </div>
    ) : (
      <div className="flex aspect-video items-center justify-center rounded-xl bg-slate-900">
        <Spinner className="text-slate-400" label={video.error ?? 'Preparing video for playback…'} />
      </div>
    )
  }
  return <ProtectedImage requestPath={path} alt={evidence.file_name ?? 'evidence'} className="aspect-video rounded-xl" eager />
}

export function EvidencePage() {
  const queryClient = useQueryClient()
  const cameras = useCameras()
  const { canUploadEvidence, isAdmin } = usePermissions()
  const [page, setPage] = useState(0)
  // Every filter change returns to the first page.
  const withReset = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value)
    setPage(0)
  }
  const [cameraId, setCameraIdRaw] = useState('')
  const [type, setTypeRaw] = useState('')
  const [storage, setStorageRaw] = useState<'all' | 'hot' | 'archived'>('all')
  const [alertId, setAlertIdRaw] = useState('')
  const setCameraId = withReset(setCameraIdRaw)
  const setType = withReset(setTypeRaw)
  const setStorage = withReset(setStorageRaw)
  const setAlertId = withReset(setAlertIdRaw)
  const [selected, setSelected] = useState<Evidence | null>(null)
  const [verification, setVerification] = useState<Record<number, EvidenceVerifyResponse>>({})
  const [uploadOpen, setUploadOpen] = useState(false)
  const [analyze, setAnalyze] = useState<{ evidenceId: number | undefined } | null>(null)
  const [confirm, setConfirm] = useState<{ kind: 'archive' | 'delete'; evidence: Evidence } | null>(null)

  const filters: EvidenceFilters = useMemo(
    () => ({
      camera_id: cameraId || undefined,
      evidence_type: (type || undefined) as EvidenceType | undefined,
      alert_id: alertId.trim() || undefined,
      is_hot_storage: storage === 'all' ? undefined : storage === 'hot',
      skip: page * PAGE_SIZE,
      limit: PAGE_SIZE,
    }),
    [cameraId, type, alertId, storage, page],
  )

  const list = useQuery({ queryKey: ['evidence', 'list', filters], queryFn: () => evidenceApi.list(filters), placeholderData: (previous) => previous })
  const items = list.data?.items ?? []
  const pages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE))

  const verify = useMutation({
    mutationFn: (evidenceId: number) => evidenceApi.verify(evidenceId),
    onSuccess: (result) => {
      setVerification((previous) => ({ ...previous, [result.evidence_id]: result }))
      if (result.integrity === 'valid') toast.success(`Evidence #${result.evidence_id}: SHA-256 verified`)
      else toast.error(`Evidence #${result.evidence_id}: ${result.integrity.toUpperCase()}`)
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const archive = useMutation({
    mutationFn: (evidenceId: number) => evidenceApi.archive(evidenceId),
    onSuccess: (result) => {
      toast.success(result.message)
      setConfirm(null)
      setSelected(null)
      void queryClient.invalidateQueries({ queryKey: ['evidence'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const remove = useMutation({
    mutationFn: (evidenceId: number) => evidenceApi.remove(evidenceId),
    onSuccess: (result) => {
      toast.success(result.message)
      setConfirm(null)
      setSelected(null)
      void queryClient.invalidateQueries({ queryKey: ['evidence'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const download = async (evidence: Evidence) => {
    const path = evidenceRequestPath(evidence.file_url)
    if (!path) return
    try {
      const url = await fetchProtectedObjectUrl(path)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = evidence.file_name ?? `evidence-${evidence.evidence_id}`
      anchor.click()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Download failed')
    }
  }

  const result = selected ? verification[selected.evidence_id] : undefined

  return (
    <div className="flex flex-col gap-3 p-4" data-testid="evidence-page">
      <PageHeader
        icon={<FileLock2 className="size-4" />}
        title="Evidence Locker"
        subtitle="Snapshots and clips with SHA-256 hashes computed at capture; verification re-hashes the stored file"
        actions={
          canUploadEvidence ? (
            <>
              <Button icon={<Upload className="size-3.5" />} onClick={() => setUploadOpen(true)}>
                Upload evidence
              </Button>
              <Button variant="primary" icon={<ScanSearch className="size-3.5" />} onClick={() => setAnalyze({ evidenceId: undefined })} data-testid="upload-analyze">
                Upload &amp; Analyze
              </Button>
            </>
          ) : null
        }
      />
      <Card className="flex flex-wrap items-end gap-2 p-3">
        <Select value={cameraId} onChange={(event) => setCameraId(event.target.value)} className="h-8 w-36 text-xs" aria-label="Camera">
          <option value="">All cameras</option>
          {(cameras.data?.items ?? []).map((camera) => (
            <option key={camera.camera_id} value={camera.camera_id}>
              {camera.camera_id}
            </option>
          ))}
        </Select>
        <Select value={type} onChange={(event) => setType(event.target.value)} className="h-8 w-40 text-xs" aria-label="Evidence type">
          <option value="">All types</option>
          {(['snapshot', 'video_clip', 'manual_snapshot', 'uploaded_video'] as EvidenceType[]).map((value) => (
            <option key={value} value={value}>
              {titleCase(value)}
            </option>
          ))}
        </Select>
        <Select value={storage} onChange={(event) => setStorage(event.target.value as typeof storage)} className="h-8 w-36 text-xs" aria-label="Storage tier">
          <option value="all">All storage</option>
          <option value="hot">Hot storage</option>
          <option value="archived">Archived</option>
        </Select>
        <Input value={alertId} onChange={(event) => setAlertId(event.target.value)} placeholder="Alert ID" className="h-8 w-72 font-mono text-xs" aria-label="Alert ID" />
        <div className="ml-auto flex items-center gap-1 text-xs text-muted">
          {list.data?.total ?? 0} items · page {page + 1}/{pages}
          <Button size="xs" icon={<ChevronLeft className="size-3" />} disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page" />
          <Button size="xs" icon={<ChevronRight className="size-3" />} disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} aria-label="Next page" />
        </div>
      </Card>

      {list.isLoading ? (
        <Spinner className="py-20" />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState icon={<FileLock2 className="size-8" />} title="No evidence" description="Snapshots are captured automatically for every alert." />
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-6">
          {items.map((evidence) => {
            const isVideo = VIDEO_TYPES.includes(evidence.evidence_type)
            const check = verification[evidence.evidence_id]
            return (
              <button
                type="button"
                key={evidence.evidence_id}
                onClick={() => setSelected(evidence)}
                className="overflow-hidden rounded-xl border border-line bg-white text-left shadow-sm transition-shadow hover:shadow-md"
              >
                {isVideo ? (
                  <div className="relative flex aspect-video items-center justify-center bg-slate-900 text-slate-400">
                    <span className="flex size-11 items-center justify-center rounded-full border border-white/20 bg-white/10 text-white">
                      <Play className="ml-0.5 size-5" />
                    </span>
                    <span className="absolute left-2 top-2 flex items-center gap-1 text-[10px] text-slate-400">
                      <FileVideo className="size-3.5" /> VIDEO
                    </span>
                    <span className="absolute bottom-1.5 right-1.5 rounded bg-black/75 px-1.5 font-mono text-[10px] font-semibold text-white">
                      {clock(evidence.duration_seconds)}
                    </span>
                  </div>
                ) : (
                  <ProtectedImage requestPath={evidence.file_exists ? evidenceRequestPath(evidence.file_url) : null} alt={evidence.file_name ?? 'evidence'} className="aspect-video" />
                )}
                <div className="space-y-1 p-2.5">
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-mono text-[11px] font-semibold">#{evidence.evidence_id}</span>
                    <Badge className={evidence.is_hot_storage ? 'bg-cyan-50 text-cyan-700 ring-cyan-200' : 'bg-slate-100 text-slate-600 ring-slate-200'}>
                      {evidence.is_hot_storage ? 'Hot' : 'Archived'}
                    </Badge>
                  </div>
                  <p className="truncate text-[11px] text-slate-600">{titleCase(evidence.evidence_type)} · {evidence.camera_id}</p>
                  <p className="font-mono text-[10px] text-muted" title={evidence.file_hash}>
                    {shortHash(evidence.file_hash, 6)}
                  </p>
                  {check ? (
                    <p className={cn('flex items-center gap-1 text-[10px] font-semibold', check.integrity === 'valid' ? 'text-green-600' : 'text-red-600')}>
                      {check.integrity === 'valid' ? <ShieldCheck className="size-3" /> : <ShieldX className="size-3" />}
                      {check.integrity.toUpperCase()}
                    </p>
                  ) : null}
                </div>
              </button>
            )
          })}
        </div>
      )}

      <Modal
        open={selected !== null}
        onOpenChange={(open) => (open ? undefined : setSelected(null))}
        size="lg"
        icon={<FileLock2 className="size-5" />}
        title={selected ? `Evidence #${selected.evidence_id}` : ''}
        description={selected ? `${titleCase(selected.evidence_type)} · ${selected.camera_id} · ${formatDateTime(selected.created_at)}` : undefined}
        footer={
          selected ? (
            <>
              {isAdmin && selected.is_hot_storage ? (
                <Button icon={<Archive className="size-3.5" />} onClick={() => setConfirm({ kind: 'archive', evidence: selected })}>
                  Archive
                </Button>
              ) : null}
              {isAdmin ? (
                <Button variant="ghost" className="text-red-600 hover:bg-red-50" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirm({ kind: 'delete', evidence: selected })}>
                  Delete
                </Button>
              ) : null}
              {canUploadEvidence && VIDEO_TYPES.includes(selected.evidence_type) && selected.file_exists ? (
                <Button variant="hud" icon={<ScanSearch className="size-3.5" />} onClick={() => setAnalyze({ evidenceId: selected.evidence_id })}>
                  Analyze
                </Button>
              ) : null}
              <Button icon={<Download className="size-3.5" />} disabled={!selected.file_exists} onClick={() => void download(selected)}>
                Download
              </Button>
              <Button variant="primary" icon={<ShieldCheck className="size-3.5" />} loading={verify.isPending} onClick={() => verify.mutate(selected.evidence_id)} data-testid="verify-evidence">
                Verify integrity
              </Button>
            </>
          ) : null
        }
      >
        {selected ? (
          <div className="flex flex-col gap-4">
            <EvidenceViewer key={selected.evidence_id} evidence={selected} />
            {result ? (
              <div className={cn('rounded-xl p-3 text-xs', result.integrity === 'valid' ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800')} role="status">
                <p className="flex items-center gap-1.5 font-semibold">
                  {result.integrity === 'valid' ? <ShieldCheck className="size-4" /> : <ShieldX className="size-4" />}
                  Integrity {result.integrity.toUpperCase()} · verified {formatDateTime(result.verified_at)}
                </p>
                <p className="mt-1 break-all font-mono text-[10px]">stored&nbsp;&nbsp;&nbsp;{result.stored_hash}</p>
                <p className="break-all font-mono text-[10px]">computed {result.computed_hash ?? '—'}</p>
              </div>
            ) : null}
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs md:grid-cols-3">
              <div>
                <dt className="text-muted">File</dt>
                <dd className="break-all font-mono">{selected.file_name ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-muted">Size</dt>
                <dd className="font-mono">{formatBytes(selected.file_size_bytes)}</dd>
              </div>
              <div>
                <dt className="text-muted">Duration</dt>
                <dd className="font-mono">{selected.duration_seconds ? formatDuration(selected.duration_seconds) : '—'}</dd>
              </div>
              <div>
                <dt className="text-muted">Alert</dt>
                <dd className="break-all font-mono">{selected.alert_id ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-muted">Track</dt>
                <dd className="font-mono">{selected.track_id ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-muted">Storage</dt>
                <dd>{selected.is_hot_storage ? 'Hot' : `Archived ${formatDateTime(selected.archived_at)}`}</dd>
              </div>
              <div className="col-span-full">
                <dt className="text-muted">SHA-256</dt>
                <dd className="break-all font-mono">{selected.file_hash}</dd>
              </div>
            </dl>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(open) => (open ? undefined : setConfirm(null))}
        title={confirm?.kind === 'archive' ? 'Archive evidence?' : 'Delete evidence?'}
        description={
          confirm?.kind === 'archive'
            ? 'The file moves from hot storage to the archive. Its hash and record are kept. The action is audit logged.'
            : 'The evidence record and file are permanently removed. This cannot be undone and is audit logged.'
        }
        confirmLabel={confirm?.kind === 'archive' ? 'Archive' : 'Delete permanently'}
        danger={confirm?.kind === 'delete'}
        loading={archive.isPending || remove.isPending}
        onConfirm={() => {
          if (!confirm) return
          if (confirm.kind === 'archive') archive.mutate(confirm.evidence.evidence_id)
          else remove.mutate(confirm.evidence.evidence_id)
        }}
      />

      {analyze ? (
        <AnalyzeVideoDialog
          open
          onOpenChange={(open) => {
            if (!open) setAnalyze(null)
          }}
          cameraIds={(cameras.data?.items ?? []).map((camera) => camera.camera_id)}
          {...(analyze.evidenceId !== undefined ? { evidenceId: analyze.evidenceId } : {})}
        />
      ) : null}
      <UploadEvidenceDialog open={uploadOpen} onOpenChange={setUploadOpen} cameraIds={(cameras.data?.items ?? []).map((camera) => camera.camera_id)} />
    </div>
  )
}

function UploadEvidenceDialog({ open, onOpenChange, cameraIds }: { open: boolean; onOpenChange: (open: boolean) => void; cameraIds: string[] }) {
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [cameraId, setCameraId] = useState('')
  const [alertId, setAlertId] = useState('')
  const [trackId, setTrackId] = useState('')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('Choose a file')
      return evidenceApi.upload(
        file,
        { camera_id: cameraId, alert_id: alertId.trim() || undefined, track_id: trackId.trim() ? Number(trackId) : undefined },
        setProgress,
      )
    },
    onSuccess: (response) => {
      toast.success(`Uploaded #${response.evidence_id} · SHA-256 ${shortHash(response.file_hash, 6)}`)
      void queryClient.invalidateQueries({ queryKey: ['evidence'] })
      close(false)
    },
    onError: (caught: Error) => setError(caught.message),
  })

  function close(next: boolean) {
    onOpenChange(next)
    if (!next) {
      setFile(null)
      setAlertId('')
      setTrackId('')
      setProgress(0)
      setError(null)
      upload.reset()
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (!file) return setError('Choose a file')
    if (file.size > MAX_UPLOAD_BYTES) return setError('Files are limited to 500 MB')
    if (!cameraId) return setError('Select the camera the footage belongs to')
    if (trackId.trim() && !/^\d{1,9}$/.test(trackId.trim())) return setError('Track ID must be a number')
    upload.mutate()
  }

  return (
    <Modal
      open={open}
      onOpenChange={close}
      size="sm"
      icon={<Upload className="size-5" />}
      title="Upload evidence"
      description="MP4, AVI, MOV, MKV, JPG or PNG up to 500 MB. The server hashes the file immediately (SHA-256)."
      footer={
        <>
          <Button onClick={() => close(false)} disabled={upload.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="upload-evidence" loading={upload.isPending}>
            Upload
          </Button>
        </>
      }
    >
      <form id="upload-evidence" onSubmit={submit} className="flex flex-col gap-3">
        <Field label="File" htmlFor="evidence-file">
          <input
            id="evidence-file"
            ref={fileRef}
            type="file"
            accept={ALLOWED_UPLOAD}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="text-xs file:mr-3 file:rounded-md file:border-0 file:bg-slate-900 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-white"
          />
        </Field>
        {file ? <p className="text-xs text-muted">{file.name} · {formatBytes(file.size)}</p> : null}
        <Field label="Camera" htmlFor="evidence-camera">
          <Select id="evidence-camera" value={cameraId} onChange={(event) => setCameraId(event.target.value)}>
            <option value="">Select camera…</option>
            {cameraIds.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Alert ID (optional)" htmlFor="evidence-alert">
            <Input id="evidence-alert" value={alertId} maxLength={40} onChange={(event) => setAlertId(event.target.value)} className="font-mono text-xs" />
          </Field>
          <Field label="Track ID (optional)" htmlFor="evidence-track">
            <Input id="evidence-track" value={trackId} inputMode="numeric" onChange={(event) => setTrackId(event.target.value)} />
          </Field>
        </div>
        {upload.isPending ? (
          <div className="h-2 overflow-hidden rounded-full bg-slate-100">
            <div className="h-full bg-cyan-500 transition-[width]" style={{ width: `${progress}%` }} />
          </div>
        ) : null}
        {error ? (
          <p className="text-xs text-red-600" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </Modal>
  )
}

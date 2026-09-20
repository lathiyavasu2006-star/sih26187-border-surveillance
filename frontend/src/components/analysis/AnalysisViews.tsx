import { Bell, Car, CheckCircle2, Loader2, Radio, ShieldAlert, Users, XCircle } from 'lucide-react'
import { ProgressBar } from '@/components/ui/primitives'
import { cn, formatDuration } from '@/lib/utils'
import type { AnalysisJob } from '@/types'

function formatClock(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

const STATUS_LABEL: Record<AnalysisJob['status'], string> = {
  queued: 'Queued…',
  loading: 'Loading YOLOv8x detector…',
  running: 'Processing…',
  complete: 'Complete',
  failed: 'Failed',
}

export function JobProgress({ job }: { job: AnalysisJob }) {
  return (
    <div className="flex flex-col gap-3" data-testid="analysis-progress">
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 font-semibold text-slate-800">
          {job.status === 'complete' ? (
            <CheckCircle2 className="size-4 text-green-600" />
          ) : job.status === 'failed' ? (
            <XCircle className="size-4 text-red-600" />
          ) : (
            <Loader2 className="size-4 animate-spin text-cyan-700" />
          )}
          {STATUS_LABEL[job.status]}
        </span>
        <span className="font-mono text-muted">
          {job.frames_total ? `${job.frames_read}/${job.frames_total} frames` : `${job.frames_read} frames`} · {job.percent.toFixed(0)}%
        </span>
      </div>
      <ProgressBar value={job.percent} tone={job.status === 'failed' ? 'red' : job.status === 'complete' ? 'green' : 'cyan'} className="h-2" />
      <div className="grid grid-cols-3 gap-2 text-center text-xs" data-testid="analysis-live-stats">
        {[
          { icon: <Users className="size-3.5" />, label: 'Persons', value: job.persons },
          { icon: <Car className="size-3.5" />, label: 'Vehicles', value: job.vehicles },
          { icon: <Bell className="size-3.5" />, label: 'Alerts', value: job.alerts },
        ].map((item) => (
          <div key={item.label} className="rounded-lg border border-line bg-slate-50 px-2 py-2">
            <p className="flex items-center justify-center gap-1 text-muted">
              {item.icon}
              {item.label}
            </p>
            <p className="mt-0.5 font-mono text-lg font-semibold text-slate-900">{item.value}</p>
          </div>
        ))}
      </div>
      {job.error ? (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
          {job.error}
        </p>
      ) : null}
    </div>
  )
}

export function JobSummary({ job }: { job: AnalysisJob }) {
  const summary = job.summary
  if (!summary) return null
  return (
    <div className="rounded-xl border border-green-200 bg-green-50/60 p-4" data-testid="analysis-summary">
      <p className="text-sm font-semibold text-slate-900">Analysis complete</p>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-muted">Persons found</dt>
          <dd className="font-mono text-base font-semibold">{summary.persons_found}</dd>
        </div>
        <div>
          <dt className="text-muted">Vehicles found</dt>
          <dd className="font-mono text-base font-semibold">{summary.vehicles_found}</dd>
        </div>
        <div>
          <dt className="text-muted">Animals found</dt>
          <dd className="font-mono text-base font-semibold">{summary.animals_found}</dd>
        </div>
        <div>
          <dt className="text-muted">Alerts generated</dt>
          <dd className="font-mono text-base font-semibold">
            {summary.alerts_created}
            {summary.alerts_detected > summary.alerts_created ? <span className="ml-1 text-[10px] font-normal text-muted">({summary.alerts_detected} detections, high-risk+ alerted)</span> : null}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Evidence saved</dt>
          <dd className="font-mono text-base font-semibold">{summary.evidence_saved} snapshot{summary.evidence_saved === 1 ? '' : 's'}</dd>
        </div>
        <div>
          <dt className="text-muted">Video length</dt>
          <dd className="font-mono text-base font-semibold">{summary.duration_seconds !== null ? formatDuration(summary.duration_seconds) : '—'}</dd>
        </div>
      </dl>
      <div className="mt-3">
        <p className="flex items-center gap-1 text-xs font-semibold text-slate-700">
          <ShieldAlert className="size-3.5 text-red-600" /> High risk moments
        </p>
        {summary.high_risk_moments.length === 0 ? (
          <p className="mt-1 text-xs text-muted">None — no moment exceeded risk 60.</p>
        ) : (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {summary.high_risk_moments.map((moment) => (
              <span key={`${moment.start_seconds}-${moment.end_seconds}`} className="rounded-md bg-red-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-red-800">
                {formatClock(moment.start_seconds)}
                {moment.end_seconds > moment.start_seconds ? `–${formatClock(moment.end_seconds)}` : ''} · R{moment.peak_risk}
              </span>
            ))}
          </div>
        )}
      </div>
      {job.standalone ? <p className="mt-3 text-[11px] text-muted">Standalone analysis: nothing was stored and the uploaded file has been deleted.</p> : null}
    </div>
  )
}

/** Annotated frames streamed from the analysis worker with the job's live counters on top. */
export function LiveAnalysisView({ job, frame, live, className }: { job: AnalysisJob | null; frame: string | null; live: boolean; className?: string }) {
  const running = job !== null && (job.status === 'queued' || job.status === 'loading' || job.status === 'running')
  return (
    <div className={cn('relative aspect-video overflow-hidden rounded-xl border border-slate-800 bg-command', className)} data-testid="analysis-live-view">
      {frame ? (
        <img src={`data:image/jpeg;base64,${frame}`} alt="Frame being analysed" className="h-full w-full object-contain" draggable={false} />
      ) : (
        <div className="hud-grid-bg flex h-full flex-col items-center justify-center gap-2 font-mono text-xs text-slate-400">
          {running ? <Loader2 className="size-6 animate-spin text-hud" /> : null}
          {job?.status === 'loading' ? 'LOADING YOLOv8x DETECTOR…' : running ? 'WAITING FOR THE FIRST FRAME…' : 'NO ANALYSIS RUNNING'}
        </div>
      )}
      {job ? (
        <>
          <div className="absolute left-2 top-2 flex items-center gap-1.5 rounded-full bg-slate-950/85 px-2.5 py-1 font-mono text-[11px] font-semibold text-white">
            {running ? <span className="size-2 animate-pulse rounded-full bg-red-500" /> : <CheckCircle2 className="size-3.5 text-green-400" />}
            {running ? (live ? 'LIVE ANALYSIS' : 'ANALYSING') : job.status === 'failed' ? 'FAILED' : 'COMPLETE'}
            {live ? <Radio className="size-3 text-hud" aria-label="streaming" /> : null}
          </div>
          <div className="absolute right-2 top-2 rounded-full bg-slate-950/85 px-2.5 py-1 font-mono text-[11px] font-semibold text-hud">
            {job.percent.toFixed(0)}% · {formatDuration(job.video_seconds)}
          </div>
          <div className="absolute inset-x-2 bottom-2 flex items-center gap-2 rounded-lg bg-slate-950/85 px-3 py-1.5 font-mono text-[11px] text-white">
            <span>PERSONS {job.persons}</span>
            <span className="text-slate-500">·</span>
            <span>VEHICLES {job.vehicles}</span>
            <span className="text-slate-500">·</span>
            <span>ANIMALS {job.animals}</span>
            <span className="text-slate-500">·</span>
            <span className={job.alerts ? 'text-red-400' : ''}>ALERTS {job.alerts}</span>
            <div className="ml-auto h-1.5 w-32 overflow-hidden rounded-full bg-white/15">
              <div className="h-full rounded-full bg-hud transition-[width] duration-300" style={{ width: `${job.percent}%` }} />
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}

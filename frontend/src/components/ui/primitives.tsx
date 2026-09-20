import * as Dialog from '@radix-ui/react-dialog'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { AlertTriangle, Loader2, X } from 'lucide-react'
import {
  forwardRef,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import { RISK_META } from '@/lib/constants'
import { cn, titleCase } from '@/lib/utils'
import type { RiskLevel } from '@/types'

// ------------------------------------------------------------------ Button

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'hud'
type ButtonSize = 'xs' | 'sm' | 'md'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-slate-900 text-white hover:bg-slate-800 disabled:bg-slate-400',
  secondary: 'border border-line bg-white text-slate-800 hover:bg-slate-50 disabled:text-slate-400',
  ghost: 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:text-slate-300',
  danger: 'bg-red-600 text-white hover:bg-red-700 disabled:bg-red-300',
  hud: 'border border-cyan-400/60 bg-cyan-400/10 text-cyan-800 hover:bg-cyan-400/20 disabled:opacity-50',
}
const BUTTON_SIZES: Record<ButtonSize, string> = {
  xs: 'h-7 gap-1 px-2 text-xs',
  sm: 'h-8 gap-1.5 px-3 text-xs',
  md: 'h-9 gap-2 px-4 text-sm',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'sm', loading = false, icon, className, children, disabled, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-lg font-medium transition-colors disabled:cursor-not-allowed',
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    >
      {loading ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  )
})

// ------------------------------------------------------------------ Badge

export function Badge({ className, children, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset whitespace-nowrap',
        className ?? 'bg-slate-50 text-slate-700 ring-slate-200',
      )}
      {...props}
    >
      {children}
    </span>
  )
}

export function RiskBadge({ level, score, className }: { level: RiskLevel; score?: number; className?: string }) {
  const meta = RISK_META[level]
  return (
    <Badge className={cn(meta.badge, className)}>
      <span className="size-1.5 rounded-full" style={{ background: meta.color }} aria-hidden />
      {meta.label}
      {score !== undefined ? <span className="font-mono">{score}</span> : null}
    </Badge>
  )
}

const STATUS_TONE: Record<string, string> = {
  online: 'bg-green-500',
  connected: 'bg-green-500',
  open: 'bg-green-500',
  healthy: 'bg-green-500',
  degraded: 'bg-amber-500',
  standby: 'bg-amber-500',
  connecting: 'bg-amber-500',
  reconnecting: 'bg-amber-500',
  offline: 'bg-red-500',
  disconnected: 'bg-red-500',
  error: 'bg-red-500',
  denied: 'bg-red-500',
  down: 'bg-red-500',
  closed: 'bg-slate-400',
  idle: 'bg-slate-300',
}

export function StatusDot({ status, pulse = false, className }: { status: string; pulse?: boolean; className?: string }) {
  const tone = STATUS_TONE[status] ?? 'bg-slate-400'
  return (
    <span className={cn('relative inline-flex size-2', className)} aria-label={status}>
      {pulse ? <span className={cn('absolute inset-0 rounded-full animate-pulse-ring', tone)} /> : null}
      <span className={cn('relative inline-flex size-2 rounded-full', tone)} />
    </span>
  )
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge className="bg-white text-slate-700 ring-slate-200">
      <StatusDot status={status} />
      {titleCase(status)}
    </Badge>
  )
}

// ------------------------------------------------------------------ Card

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('rounded-xl border border-line bg-panel shadow-[0_1px_2px_rgb(15_23_42/0.04)]', className)} {...props} />
}

export function CardHeader({
  title,
  subtitle,
  actions,
  icon,
  className,
}: {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  icon?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex items-center justify-between gap-3 border-b border-line px-4 py-3', className)}>
      <div className="flex min-w-0 items-center gap-2.5">
        {icon ? <span className="text-slate-500">{icon}</span> : null}
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-slate-900">{title}</h3>
          {subtitle ? <p className="truncate text-xs text-muted">{subtitle}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  )
}

// ------------------------------------------------------------------ States

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <div className={cn('flex items-center justify-center gap-2 text-sm text-muted', className)} role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label ?? 'Loading…'}
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode
  title: string
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 px-6 py-10 text-center', className)}>
      {icon ? <div className="mb-1 text-slate-300">{icon}</div> : null}
      <p className="text-sm font-semibold text-slate-700">{title}</p>
      {description ? <div className="max-w-md text-xs text-muted">{description}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const message = error instanceof Error ? error.message : 'Something went wrong'
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 px-6 py-8 text-center', className)} role="alert">
      <AlertTriangle className="size-6 text-red-500" aria-hidden />
      <p className="text-sm font-semibold text-slate-800">Could not load data</p>
      <p className="max-w-md text-xs text-muted">{message}</p>
      {onRetry ? (
        <Button size="xs" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  )
}

// ------------------------------------------------------------------ Form controls

const CONTROL =
  'h-9 w-full rounded-lg border border-line bg-white px-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-cyan-600 focus:outline-none focus:ring-2 focus:ring-cyan-500/20 disabled:bg-slate-50 disabled:text-slate-400'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(CONTROL, className)} {...props} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(CONTROL, 'h-auto min-h-20 py-2', className)} {...props} />
})

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <select ref={ref} className={cn(CONTROL, 'pr-8', className)} {...props}>
      {children}
    </select>
  )
})

export function Field({
  label,
  hint,
  error,
  children,
  className,
  htmlFor,
}: {
  label: string
  hint?: ReactNode
  error?: string | null
  children: ReactNode
  className?: string
  htmlFor?: string
}) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={htmlFor} className="text-xs font-semibold text-slate-700">
        {label}
      </label>
      {children}
      {error ? <p className="text-xs text-red-600">{error}</p> : hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  )
}

export function Switch({
  checked,
  onCheckedChange,
  label,
  disabled,
}: {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  label: string
  disabled?: boolean
}) {
  return (
    <SwitchPrimitive.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      aria-label={label}
      className="relative h-5 w-9 shrink-0 rounded-full bg-slate-300 transition-colors data-[state=checked]:bg-cyan-600 disabled:opacity-50"
    >
      <SwitchPrimitive.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
    </SwitchPrimitive.Root>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>
}

// ------------------------------------------------------------------ Tooltip

export function Tip({ label, children, side = 'bottom' }: { label: ReactNode; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  return (
    <TooltipPrimitive.Root delayDuration={250}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className="z-[1200] rounded-md bg-slate-900 px-2 py-1 text-[11px] font-medium text-white shadow-lg"
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}

// ------------------------------------------------------------------ Modal

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = 'md',
  tone = 'light',
  icon,
  surfaceStyle,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description?: ReactNode
  children: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
  tone?: 'light' | 'hud'
  icon?: ReactNode
  /** Background override for the dialog surface (e.g. the Event DNA deep-navy canvas). */
  surfaceStyle?: CSSProperties
}) {
  const width = { sm: 'max-w-md', md: 'max-w-2xl', lg: 'max-w-4xl', xl: 'max-w-6xl', full: 'h-[calc(100vh-2rem)] max-h-none max-w-none' }[size]
  const hud = tone === 'hud'
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[1000] bg-slate-950/50 backdrop-blur-[2px]" />
        <Dialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-[1001] flex max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl shadow-2xl focus:outline-none',
            width,
            hud ? 'border border-cyan-400/40 bg-command text-slate-100' : 'border border-line bg-white',
          )}
          style={surfaceStyle}
        >
          <div className={cn('flex items-start justify-between gap-4 border-b px-5 py-4', hud ? 'border-cyan-400/20' : 'border-line')}>
            <div className="flex min-w-0 items-start gap-3">
              {icon ? <span className={cn('mt-0.5', hud ? 'text-hud' : 'text-slate-500')}>{icon}</span> : null}
              <div className="min-w-0">
                <Dialog.Title className={cn('text-base font-semibold', hud ? 'font-mono tracking-wide text-hud' : 'text-slate-900')}>
                  {title}
                </Dialog.Title>
                {description ? (
                  <Dialog.Description className={cn('mt-0.5 text-xs', hud ? 'text-slate-400' : 'text-muted')}>
                    {description}
                  </Dialog.Description>
                ) : (
                  <Dialog.Description className="sr-only">Dialog</Dialog.Description>
                )}
              </div>
            </div>
            <Dialog.Close
              className={cn('rounded-md p-1', hud ? 'text-slate-400 hover:bg-white/10 hover:text-white' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-700')}
              aria-label="Close"
            >
              <X className="size-4" />
            </Dialog.Close>
          </div>
          <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer ? (
            <div className={cn('flex items-center justify-end gap-2 border-t px-5 py-3', hud ? 'border-cyan-400/20' : 'border-line bg-slate-50/60')}>
              {footer}
            </div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  loading,
  danger = true,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel: string
  onConfirm: () => void
  loading?: boolean
  danger?: boolean
}) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      size="sm"
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-sm text-slate-600">{description}</div>
    </Modal>
  )
}

// ------------------------------------------------------------------ Page header

export function PageHeader({
  title,
  subtitle,
  actions,
  icon,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        {icon ? <div className="flex size-9 items-center justify-center rounded-lg bg-slate-900 text-hud">{icon}</div> : null}
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">{title}</h1>
          {subtitle ? <p className="text-xs text-muted">{subtitle}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

export function StatCard({
  label,
  value,
  sub,
  icon,
  tone = 'default',
  loading,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  icon?: ReactNode
  tone?: 'default' | 'critical' | 'warning' | 'good'
  loading?: boolean
}) {
  const toneClass = {
    default: 'text-slate-900',
    critical: 'text-red-600',
    warning: 'text-amber-600',
    good: 'text-green-600',
  }[tone]
  return (
    <Card className="flex items-start justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</p>
        <p className={cn('mt-1 font-mono text-2xl font-semibold tabular-nums', toneClass)}>
          {loading ? <span className="inline-block h-7 w-12 animate-pulse rounded bg-slate-100" /> : value}
        </p>
        {sub ? <p className="mt-0.5 truncate text-xs text-muted">{sub}</p> : null}
      </div>
      {icon ? <div className="rounded-lg bg-slate-100 p-2 text-slate-600">{icon}</div> : null}
    </Card>
  )
}

export function ProgressBar({ value, tone = 'cyan', className }: { value: number; tone?: 'cyan' | 'red' | 'amber' | 'green'; className?: string }) {
  const color = { cyan: 'bg-cyan-500', red: 'bg-red-500', amber: 'bg-amber-500', green: 'bg-green-500' }[tone]
  const clamped = Math.max(0, Math.min(100, value))
  return (
    <div className={cn('h-1.5 w-full overflow-hidden rounded-full bg-slate-100', className)} role="progressbar" aria-valuenow={Math.round(clamped)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cn('h-full rounded-full transition-[width] duration-500', color)} style={{ width: `${clamped}%` }} />
    </div>
  )
}

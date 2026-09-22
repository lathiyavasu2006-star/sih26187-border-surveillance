import { ShieldCheck, Swords } from 'lucide-react'
import { cn } from '@/lib/utils'

/** The two alerting classes of the weapon model; always listed so a zero is visible, not just absent. */
const KNOWN_WEAPONS: Record<string, { label: string; hint: string }> = {
  firearm: { label: 'Firearm', hint: 'pistol / handgun' },
  knife: { label: 'Knife', hint: 'blade' },
}

/**
 * How many weapons were seen, by type. `counts` is the number of distinct people seen carrying each type (a pistol
 * in view for 200 frames is one armed person, not 200 weapons); `sightings` optionally adds how many frames it
 * appeared in, which tells the operator how solid each finding is.
 */
export function WeaponCountBox({
  counts,
  sightings,
  title = 'Weapons detected',
  unit = 'armed person',
  tone = 'light',
  showNote = true,
  className,
}: {
  counts: Record<string, number>
  sightings?: Record<string, number>
  title?: string
  unit?: string
  tone?: 'light' | 'hud'
  /** The coverage note (what the model cannot detect); hide it when a sibling box already shows it. */
  showNote?: boolean
  className?: string
}) {
  const types = [...new Set([...Object.keys(KNOWN_WEAPONS), ...Object.keys(counts)])]
  const total = types.reduce((sum, type) => sum + (counts[type] ?? 0), 0)
  const hud = tone === 'hud'
  const armed = total > 0

  return (
    <div
      className={cn(
        'rounded-xl border p-3',
        armed ? (hud ? 'border-red-500/60 bg-red-950/40' : 'border-red-300 bg-red-50') : hud ? 'border-slate-700 bg-slate-900/60' : 'border-line bg-white',
        className,
      )}
      data-testid="weapon-count-box"
      data-total={total}
    >
      <div className="flex items-center justify-between gap-2">
        <p className={cn('flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider', armed ? 'text-red-700' : hud ? 'text-slate-300' : 'text-slate-700', hud && armed && 'text-red-300')}>
          {armed ? <Swords className="size-3.5" /> : <ShieldCheck className="size-3.5 text-green-600" />}
          {title}
        </p>
        <span className={cn('font-mono text-lg font-bold', armed ? (hud ? 'text-red-300' : 'text-red-700') : hud ? 'text-slate-400' : 'text-slate-500')} data-testid="weapon-total">
          {total}
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-2">
        {types.map((type) => {
          const count = counts[type] ?? 0
          const meta = KNOWN_WEAPONS[type] ?? { label: type.charAt(0).toUpperCase() + type.slice(1), hint: '' }
          const frames = sightings?.[type]
          return (
            <div
              key={type}
              className={cn(
                'rounded-lg border px-2.5 py-2',
                count > 0 ? (hud ? 'border-red-500/50 bg-red-500/15' : 'border-red-200 bg-white') : hud ? 'border-slate-800 bg-slate-950/50' : 'border-slate-100 bg-slate-50',
              )}
              data-testid={`weapon-${type}`}
            >
              <p className={cn('text-[11px] font-semibold', count > 0 ? (hud ? 'text-red-200' : 'text-red-800') : hud ? 'text-slate-400' : 'text-slate-500')}>
                {meta.label}
                {meta.hint ? <span className="ml-1 font-normal opacity-70">{meta.hint}</span> : null}
              </p>
              <p className={cn('font-mono text-base font-bold', count > 0 ? (hud ? 'text-red-300' : 'text-red-700') : hud ? 'text-slate-500' : 'text-slate-400')}>{count}</p>
              {frames ? <p className={cn('text-[10px]', hud ? 'text-slate-400' : 'text-muted')}>seen in {frames} frame{frames === 1 ? '' : 's'}</p> : null}
            </div>
          )
        })}
      </div>

      {showNote ? <p className={cn('mt-2 text-[10px] leading-snug', hud ? 'text-slate-500' : 'text-muted')}>
        {armed ? `Counted per ${unit}, not per frame. ` : 'No weapon confirmed. '}
        Detects handguns and knives. Rifles, sticks and rods are not trained yet, and small far-away objects on night CCTV are
        often not resolvable.
      </p> : null}
    </div>
  )
}

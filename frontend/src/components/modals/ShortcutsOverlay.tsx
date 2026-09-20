import { Keyboard } from 'lucide-react'
import { Modal } from '@/components/ui/primitives'
import { SHORTCUTS, type ShortcutGroup } from '@/hooks/useKeyboard'

const GROUPS: ShortcutGroup[] = ['Navigation', 'Camera', 'Map', 'Security', 'Alerts', 'Analysis']

export function ShortcutsOverlay({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      size="xl"
      tone="hud"
      icon={<Keyboard className="size-5" />}
      title="KEYBOARD SHORTCUTS"
      description="Press ? to toggle · Esc to close · shortcuts are ignored while typing in a text field"
    >
      <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 xl:grid-cols-4" data-testid="shortcuts-overlay">
        {GROUPS.map((group) => (
          <section key={group}>
            <h3 className="mb-2 border-b border-cyan-400/20 pb-1.5 font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-hud">{group}</h3>
            <ul className="flex flex-col gap-1.5">
              {SHORTCUTS.filter((shortcut) => shortcut.group === group).map((shortcut) => (
                <li key={shortcut.combo} className="grid grid-cols-[auto_1fr] items-center gap-3 text-xs">
                  <span className="flex min-w-24 shrink-0 flex-wrap items-center gap-1">
                    {shortcut.keys.map((key, index) => (
                      <span key={`${key}-${index}`} className="flex items-center gap-1">
                        {index > 0 && shortcut.keys.length > 1 && shortcut.keys.every((k) => k.length === 1) ? (
                          <span className="text-slate-500">/</span>
                        ) : index > 0 ? (
                          <span className="text-slate-500">+</span>
                        ) : null}
                        <kbd className="inline-flex min-w-6 items-center justify-center rounded-md border border-cyan-400/40 bg-cyan-400/10 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-cyan-100">
                          {key}
                        </kbd>
                      </span>
                    ))}
                  </span>
                  <span className="text-slate-300">{shortcut.description}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Modal>
  )
}

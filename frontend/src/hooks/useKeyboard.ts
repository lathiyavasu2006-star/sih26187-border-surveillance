import { useEffect, useRef } from 'react'

export type ShortcutGroup = 'Navigation' | 'Camera' | 'Map' | 'Security' | 'Alerts' | 'Analysis'

export interface ShortcutDescriptor {
  combo: string
  keys: string[]
  description: string
  group: ShortcutGroup
}

/** Single source of truth for the shortcuts overlay (?) and the handlers registered by the shell and pages. */
export const SHORTCUTS: ShortcutDescriptor[] = [
  { combo: 'ctrl+d', keys: ['Ctrl', 'D'], description: 'Dashboard', group: 'Navigation' },
  { combo: 'ctrl+m', keys: ['Ctrl', 'M'], description: 'Map', group: 'Navigation' },
  { combo: 'ctrl+f', keys: ['Ctrl', 'F'], description: 'Search', group: 'Navigation' },
  { combo: 'ctrl+b', keys: ['Ctrl', 'B'], description: 'Pin / unpin sidebar', group: 'Navigation' },
  { combo: 'ctrl+t', keys: ['Ctrl', 'T'], description: 'New tab (Alt+T if the browser keeps Ctrl+T)', group: 'Navigation' },
  { combo: 'alt+w', keys: ['Alt', 'W'], description: 'Close tab', group: 'Navigation' },
  { combo: 'alt+arrowright', keys: ['Alt', '→'], description: 'Next / previous tab (Alt+←)', group: 'Navigation' },
  { combo: 'alt+1', keys: ['Alt', '1…0'], description: 'Jump to page 1–10 in the sidebar', group: 'Navigation' },
  { combo: 'alt+r', keys: ['Alt', 'R'], description: 'Right panel', group: 'Navigation' },
  { combo: 'alt+v', keys: ['Alt', 'V'], description: 'Video analysis', group: 'Navigation' },
  { combo: 'f', keys: ['F'], description: 'Fullscreen camera', group: 'Camera' },
  { combo: 's', keys: ['S'], description: 'Snapshot to evidence', group: 'Camera' },
  { combo: 'r', keys: ['R'], description: 'Record / stop recording', group: 'Camera' },
  { combo: '1', keys: ['1–4'], description: 'Grid layout', group: 'Camera' },
  { combo: ' ', keys: ['Space'], description: 'Pause / resume feed', group: 'Camera' },
  { combo: '[', keys: ['[', ']'], description: 'Previous / next camera', group: 'Camera' },
  { combo: 'h', keys: ['H'], description: 'Tactical HUD overlay (camera pages)', group: 'Camera' },
  { combo: 'm', keys: ['M'], description: 'Cycle map styles', group: 'Map' },
  { combo: 'h@map', keys: ['H'], description: 'Threat heatmap', group: 'Map' },
  { combo: 'v', keys: ['V'], description: 'Geo-fence rings on every camera', group: 'Map' },
  { combo: 'g', keys: ['G'], description: 'India view', group: 'Map' },
  { combo: 'n', keys: ['N', 'E', 'W', 'S'], description: 'Border regions', group: 'Map' },
  { combo: '+', keys: ['+', '−'], description: 'Zoom in / out', group: 'Map' },
  { combo: 'ctrl+l', keys: ['Ctrl', 'L'], description: 'Lock screen', group: 'Security' },
  { combo: 'alt+l', keys: ['Alt', 'L'], description: 'Lock screen (when the browser keeps Ctrl+L)', group: 'Security' },
  { combo: 'f12', keys: ['F12'], description: 'Panic button', group: 'Security' },
  { combo: 'ctrl+shift+l', keys: ['Ctrl', 'Shift', 'L'], description: 'Logout', group: 'Security' },
  { combo: 'autolock', keys: ['5 min'], description: 'Auto-lock after inactivity', group: 'Security' },
  { combo: '?', keys: ['?'], description: 'This help', group: 'Security' },
  { combo: 'a', keys: ['A'], description: 'Latest alert', group: 'Alerts' },
  { combo: 'enter', keys: ['Enter'], description: 'Acknowledge selected alert', group: 'Alerts' },
  { combo: 'f8', keys: ['F8'], description: 'Mute / unmute alarm', group: 'Alerts' },
  { combo: 'j', keys: ['J', 'K'], description: 'Next / previous alert', group: 'Alerts' },
  { combo: 'e', keys: ['E'], description: 'Event DNA of the selected track', group: 'Analysis' },
  { combo: 'c', keys: ['C'], description: 'Cross-camera correlation', group: 'Analysis' },
  { combo: 'p', keys: ['P'], description: 'Prediction view', group: 'Analysis' },
  { combo: 'u', keys: ['U'], description: 'Confidence fusion', group: 'Analysis' },
]

export type KeyHandler = (event: KeyboardEvent) => void

export interface KeyBinding {
  handler: KeyHandler
  /** Fire even while an input, textarea, select or contenteditable element has focus. */
  allowInInputs?: boolean
  /** Leave the browser's default action alone. */
  allowDefault?: boolean
}

export type KeyBindings = Record<string, KeyHandler | KeyBinding>

/** Layer priorities: page bindings shadow the shell's, dialogs shadow both. */
export const KEY_PRIORITY = { shell: 0, page: 10, dialog: 20 } as const

/** Normalizes a keyboard event to "ctrl+shift+alt+key" (modifiers in fixed order, key lower-cased). */
export function comboFromEvent(event: KeyboardEvent): string {
  let key = event.key.toLowerCase()
  // Shift+/ is "?" on US layouts; some browsers and remote sessions report key "/" with shiftKey.
  if (event.code === 'Slash' && event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) return '?'
  if (key === 'spacebar') key = ' '
  const parts: string[] = []
  if (event.ctrlKey || event.metaKey) parts.push('ctrl')
  // Printable characters already encode shift ("?", "+", "L"); only named keys and ctrl combos carry it.
  if (event.shiftKey && (key.length > 1 || event.ctrlKey || event.metaKey)) parts.push('shift')
  if (event.altKey) parts.push('alt')
  // With Alt (and on non-US layouts) `key` may be a symbol; letters and digits come from `code`.
  const code = /^(?:Key([A-Z])|Digit(\d))$/.exec(event.code)
  if ((event.altKey || event.ctrlKey || event.metaKey) && code) key = (code[1] ?? code[2] ?? key).toLowerCase()
  parts.push(key)
  return parts.join('+')
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    const type = (target as HTMLInputElement).type
    return !['checkbox', 'radio', 'button', 'submit', 'range', 'color'].includes(type)
  }
  return false
}

interface Layer {
  priority: number
  order: number
  bindings: { current: KeyBindings }
}

const layers = new Set<Layer>()
let registrations = 0
let listening = false

function dispatch(event: KeyboardEvent): void {
  if (event.isComposing) return
  const combo = comboFromEvent(event)
  // Highest priority first; among equals the most recently mounted layer wins.
  const ordered = [...layers].sort((a, b) => b.priority - a.priority || b.order - a.order)
  // While a dialog (special modal, lock screen, panic view) is open, page shortcuts must not act behind it.
  const dialogOpen = document.querySelector('[role="dialog"][data-state="open"], [role="dialog"][aria-modal="true"]') !== null
  for (const layer of ordered) {
    if (dialogOpen && layer.priority === KEY_PRIORITY.page) continue
    const binding = layer.bindings.current[combo]
    if (!binding) continue
    const { handler, allowInInputs = false, allowDefault = false } = typeof binding === 'function' ? { handler: binding } : binding
    if (!allowInInputs && isEditableTarget(event.target)) return
    if (event.repeat && combo !== '+' && combo !== '-' && combo !== '=') {
      if (!allowDefault) event.preventDefault()
      return
    }
    if (!allowDefault) event.preventDefault()
    event.stopPropagation()
    handler(event)
    return
  }
}

function ensureListener(): void {
  if (listening || typeof window === 'undefined') return
  // Capture phase: the console's shortcuts run before menus, maps or widgets can swallow the key.
  window.addEventListener('keydown', dispatch, { capture: true })
  listening = true
}

/**
 * Registers keyboard shortcuts while the component is mounted. Bindings may change on every render; the
 * latest handlers are always used. Higher `priority` layers shadow lower ones for the same key.
 */
export function useKeyboard(bindings: KeyBindings, enabled = true, priority: number = KEY_PRIORITY.page): void {
  const latest = useRef(bindings)
  useEffect(() => {
    latest.current = bindings
  })

  useEffect(() => {
    if (!enabled) return undefined
    ensureListener()
    registrations += 1
    const layer: Layer = { priority, order: registrations, bindings: latest }
    layers.add(layer)
    return () => {
      layers.delete(layer)
    }
  }, [enabled, priority])
}

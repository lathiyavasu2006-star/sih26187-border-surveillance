import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { comboFromEvent, isEditableTarget, KEY_PRIORITY, SHORTCUTS, useKeyboard, type KeyBindings } from '@/hooks/useKeyboard'

function Harness({ bindings }: { bindings: KeyBindings }) {
  useKeyboard(bindings)
  return <input aria-label="field" />
}

describe('comboFromEvent', () => {
  it('normalises modifiers and keys', () => {
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: 'l', ctrlKey: true }))).toBe('ctrl+l')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: 'L', ctrlKey: true, shiftKey: true }))).toBe('ctrl+shift+l')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: '/', code: 'Slash', shiftKey: true }))).toBe('?')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: ' ', code: 'Space' }))).toBe(' ')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: 'Enter' }))).toBe('enter')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: 'F8' }))).toBe('f8')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: '+', shiftKey: true }))).toBe('+')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: 'l', code: 'KeyL', ctrlKey: true }))).toBe('ctrl+l')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: 'F12' }))).toBe('f12')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: '?', shiftKey: true }))).toBe('?')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: '¡', code: 'Digit1', altKey: true }))).toBe('alt+1')
    expect(comboFromEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', altKey: true }))).toBe('alt+arrowright')
  })

  it('recognises editable targets', () => {
    const input = document.createElement('input')
    const checkbox = document.createElement('input')
    checkbox.type = 'checkbox'
    expect(isEditableTarget(input)).toBe(true)
    expect(isEditableTarget(checkbox)).toBe(false)
    expect(isEditableTarget(document.createElement('textarea'))).toBe(true)
    expect(isEditableTarget(document.body)).toBe(false)
  })

  it('documents every required shortcut exactly once', () => {
    const combos = SHORTCUTS.map((shortcut) => shortcut.combo)
    expect(new Set(combos).size).toBe(combos.length)
    for (const required of ['ctrl+d', 'ctrl+m', 'ctrl+f', 'ctrl+b', 'ctrl+t', 'f', 's', 'r', ' ', 'm', 'h@map', 'g', 'n', '+', 'ctrl+l', 'f12', 'ctrl+shift+l', 'a', 'enter', 'f8', '?']) {
      expect(combos).toContain(required)
    }
    expect(new Set(SHORTCUTS.map((shortcut) => shortcut.group))).toEqual(new Set(['Navigation', 'Camera', 'Map', 'Security', 'Alerts', 'Analysis']))
  })
})

describe('useKeyboard', () => {
  it('fires handlers, prevents defaults and ignores typing', () => {
    const lock = vi.fn()
    const map = vi.fn()
    render(<Harness bindings={{ 'ctrl+l': { handler: lock, allowInInputs: true }, m: map }} />)

    const event = new KeyboardEvent('keydown', { key: 'l', ctrlKey: true, cancelable: true })
    window.dispatchEvent(event)
    expect(lock).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)

    fireEvent.keyDown(window, { key: 'm' })
    expect(map).toHaveBeenCalledTimes(1)

    const field = screen.getByLabelText('field')
    fireEvent.keyDown(field, { key: 'm' })
    expect(map).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(field, { key: 'l', ctrlKey: true })
    expect(lock).toHaveBeenCalledTimes(2)
  })

  it('ignores auto-repeat', () => {
    const handler = vi.fn()
    render(<Harness bindings={{ h: handler }} />)
    fireEvent.keyDown(window, { key: 'h', repeat: true })
    expect(handler).not.toHaveBeenCalled()
  })
})

describe('keyboard layers', () => {
  it('lets page bindings shadow shell bindings for the same key', () => {
    const shell = vi.fn()
    const page = vi.fn()
    function Shell() {
      useKeyboard({ h: shell }, true, KEY_PRIORITY.shell)
      return null
    }
    function Page() {
      useKeyboard({ h: page }, true, KEY_PRIORITY.page)
      return null
    }
    const { unmount } = render(
      <>
        <Shell />
        <Page />
      </>,
    )
    fireEvent.keyDown(window, { key: 'h' })
    expect(page).toHaveBeenCalledTimes(1)
    expect(shell).not.toHaveBeenCalled()
    unmount()
  })

  it('suspends page shortcuts behind an open dialog but keeps shell security keys', () => {
    const page = vi.fn()
    const lock = vi.fn()
    function Shell() {
      useKeyboard({ 'ctrl+l': { handler: lock, allowInInputs: true } }, true, KEY_PRIORITY.shell)
      return null
    }
    function Page() {
      useKeyboard({ m: page }, true, KEY_PRIORITY.page)
      return <div role="dialog" aria-modal="true" />
    }
    const { unmount } = render(
      <>
        <Shell />
        <Page />
      </>,
    )
    fireEvent.keyDown(window, { key: 'm' })
    expect(page).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: 'l', code: 'KeyL', ctrlKey: true })
    expect(lock).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('runs in the capture phase, before a widget can stop the key', () => {
    const handler = vi.fn()
    function Harness() {
      useKeyboard({ '?': handler }, true, KEY_PRIORITY.shell)
      return <div data-testid="widget" onKeyDown={(event) => event.stopPropagation()} tabIndex={0} />
    }
    render(<Harness />)
    fireEvent.keyDown(screen.getByTestId('widget'), { key: '?', shiftKey: true })
    expect(handler).toHaveBeenCalledTimes(1)
  })
})

/**
 * Audible alerts synthesized with the Web Audio API (no media assets to load or tamper with).
 * Browsers only allow audio after a user gesture; `unlockAudio` is called on the first interaction.
 */
let context: AudioContext | null = null

function audio(): AudioContext | null {
  if (typeof window === 'undefined' || typeof window.AudioContext === 'undefined') return null
  if (!context) context = new window.AudioContext()
  return context
}

export function unlockAudio(): void {
  const ctx = audio()
  if (ctx && ctx.state === 'suspended') void ctx.resume()
}

function tone(ctx: AudioContext, frequency: number, start: number, duration: number, volume: number, type: OscillatorType) {
  const oscillator = ctx.createOscillator()
  const gain = ctx.createGain()
  oscillator.type = type
  oscillator.frequency.setValueAtTime(frequency, start)
  gain.gain.setValueAtTime(0.0001, start)
  gain.gain.exponentialRampToValueAtTime(volume, start + 0.02)
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration)
  oscillator.connect(gain).connect(ctx.destination)
  oscillator.start(start)
  oscillator.stop(start + duration + 0.05)
}

export type AlarmKind = 'critical' | 'warning' | 'info'

export function playAlarm(kind: AlarmKind): void {
  const ctx = audio()
  if (!ctx || ctx.state !== 'running') return
  const now = ctx.currentTime
  if (kind === 'critical') {
    for (let i = 0; i < 3; i += 1) {
      tone(ctx, 880, now + i * 0.32, 0.14, 0.25, 'square')
      tone(ctx, 660, now + i * 0.32 + 0.15, 0.14, 0.25, 'square')
    }
  } else if (kind === 'warning') {
    tone(ctx, 740, now, 0.18, 0.18, 'triangle')
    tone(ctx, 740, now + 0.25, 0.18, 0.18, 'triangle')
  } else {
    tone(ctx, 520, now, 0.12, 0.1, 'sine')
  }
}

import { useRef, useState } from 'react'

interface HoldToExecuteButtonProps {
  label: string
  tone: 'bull' | 'bear'
  onConfirm: () => void
  disabled?: boolean
  holdMs?: number
}

const TONE_CLASS: Record<'bull' | 'bear', { base: string; fill: string }> = {
  bull: { base: 'border-bull text-bull', fill: 'bg-bull/30' },
  bear: { base: 'border-bear text-bear', fill: 'bg-bear/30' },
}

/** Press-and-hold confirmation ("Terminal Edge" sprint, 2026-08-27) --
 * the "Hold to Execute" safety option for a one-click action, so a
 * stray tap can't fire it. Releasing before the fill completes cancels
 * outright (no partial-progress carryover on the next press). Pointer
 * events (not mouse-only) so it works the same on touch. */
export function HoldToExecuteButton({
  label,
  tone,
  onConfirm,
  disabled,
  holdMs = 900,
}: HoldToExecuteButtonProps) {
  const [holding, setHolding] = useState(false)
  const timerRef = useRef<number | null>(null)

  const start = () => {
    if (disabled) return
    setHolding(true)
    timerRef.current = window.setTimeout(() => {
      setHolding(false)
      onConfirm()
    }, holdMs)
  }

  const cancel = () => {
    setHolding(false)
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  const cls = TONE_CLASS[tone]

  return (
    <button
      type="button"
      disabled={disabled}
      onPointerDown={start}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      className={
        'relative w-full select-none overflow-hidden rounded-lg border py-2.5 text-xs font-bold uppercase tracking-wide disabled:cursor-not-allowed disabled:opacity-40 ' +
        cls.base
      }
    >
      <span
        className={'absolute inset-y-0 left-0 ' + cls.fill}
        style={{
          width: holding ? '100%' : '0%',
          transitionProperty: 'width',
          transitionDuration: holding ? `${holdMs}ms` : '150ms',
          transitionTimingFunction: 'linear',
        }}
      />
      <span className="relative z-10">{holding ? 'Hold to confirm…' : label}</span>
    </button>
  )
}

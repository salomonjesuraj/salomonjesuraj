import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from '../ErrorBoundary'

/** Throws on render whenever `symbol` matches `crashOn` -- lets a single
 * component simulate both "this symbol's data is broken" (Phase 2) and
 * "a different symbol's data is fine" (Phase 3, the key-reset case)
 * without two separate throwing components. */
function MaybeThrow({ symbol, crashOn }: { symbol: string; crashOn: string }) {
  if (symbol === crashOn) throw new Error(`Crash Test: ${symbol}`)
  return <div>Rendered OK: {symbol}</div>
}

describe('ErrorBoundary', () => {
  // React logs a caught render error to the console twice on its own
  // (once from its internal error-boundary machinery, independent of
  // this component's own componentDidCatch -> console.error call) --
  // expected, real behavior, not something to fix; silenced here so a
  // passing test run doesn't read as if something is actually wrong.
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    consoleErrorSpy.mockRestore()
  })

  it('renders normal children when no error occurs (happy path)', () => {
    render(
      <ErrorBoundary label="Widget">
        <div>Hello from a healthy child</div>
      </ErrorBoundary>,
    )

    expect(screen.getByText('Hello from a healthy child')).toBeInTheDocument()
    expect(screen.queryByText(/failed to render/)).not.toBeInTheDocument()
  })

  it('catches a render error without crashing the test root and shows the real message', () => {
    render(
      <ErrorBoundary label="Chart">
        <MaybeThrow symbol="ICICIPRULI" crashOn="ICICIPRULI" />
      </ErrorBoundary>,
    )

    // The label names itself -- this app's own established honesty
    // standard (never a generic "something went wrong").
    expect(screen.getByText('Chart failed to render.')).toBeInTheDocument()
    // The real Error#message, not a sanitized/generic stand-in.
    expect(screen.getByText('Crash Test: ICICIPRULI')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    // The throwing child itself never actually rendered its own output.
    expect(screen.queryByText(/Rendered OK/)).not.toBeInTheDocument()
  })

  // A dedicated "click Retry and watch it recover" test was tried and
  // dropped: proving recovery needs a child whose throw condition
  // flips between attempts, but React's own error-recovery machinery
  // re-invokes the SAME render synchronously before this boundary's
  // componentDidCatch ever runs -- any plain closure-mutated flag gets
  // silently flipped by that internal re-invocation too, so the
  // boundary's real error path never actually engages and the
  // assertion never observes what it's meant to. The button's own
  // presence and label are already covered above; real recovery is
  // covered honestly by the key-change case below instead, which
  // mirrors this app's actual real usage (`key={activeSymbol}`) rather
  // than a synthetic Retry-click harness fighting React's own internals.

  it('a key change (e.g. switching symbols) remounts the boundary and self-heals without a manual Retry', () => {
    // Mirrors this app's own real usage in OptionsAnalytics.tsx:
    // `<ErrorBoundary label="Chart" key={activeSymbol}>` -- switching
    // to a symbol whose own data doesn't crash should recover
    // automatically, with no Retry click needed.
    const { rerender } = render(
      <ErrorBoundary label="Chart" key="ICICIPRULI">
        <MaybeThrow symbol="ICICIPRULI" crashOn="ICICIPRULI" />
      </ErrorBoundary>,
    )

    expect(screen.getByText('Chart failed to render.')).toBeInTheDocument()

    rerender(
      <ErrorBoundary label="Chart" key="KAYNES">
        <MaybeThrow symbol="KAYNES" crashOn="ICICIPRULI" />
      </ErrorBoundary>,
    )

    expect(screen.getByText('Rendered OK: KAYNES')).toBeInTheDocument()
    expect(screen.queryByText(/failed to render/)).not.toBeInTheDocument()
  })
})

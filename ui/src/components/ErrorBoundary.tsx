import { Component, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
  // A short, human label for what this boundary wraps -- shown in the
  // fallback so a crash in one widget names itself ("Chart failed to
  // render") instead of reading as "something, somewhere, broke."
  label: string
}

interface ErrorBoundaryState {
  error: Error | null
}

/** "Black Screen Crash" fix (2026-08-29): this app had ZERO error
 * boundaries anywhere before this -- any uncaught render/effect
 * exception in any single widget unmounted the ENTIRE React tree,
 * which against this app's own near-black body background
 * (--color-hud-bg: #05070a) reads exactly as "pitch black screen," not
 * a visible error. React's own class-component lifecycle
 * (getDerivedStateFromError/componentDidCatch) is still the only way
 * to catch a render error -- there is no hooks equivalent.
 *
 * The real error message is always shown (never a generic "something
 * went wrong") -- same honesty standard as every dash/reason string
 * elsewhere in this app. `key` the boundary on whatever prop identifies
 * "a new attempt" (e.g. `key={symbol}`) to let React remount and retry
 * automatically when that identity changes, rather than requiring the
 * user to click Retry every time they switch to a different symbol. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[ErrorBoundary:${this.props.label}]`, error, info.componentStack)
  }

  private reset = (): void => {
    this.setState({ error: null })
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="rounded-xl border border-dashed border-bear/40 bg-hud-panel/40 px-4 py-6 text-center text-xs text-hud-muted">
        <p className="font-bold text-bear">{this.props.label} failed to render.</p>
        <p className="mt-1 font-mono">{error.message}</p>
        <button
          type="button"
          onClick={this.reset}
          className="mt-3 rounded-lg bg-hud-panel px-3 py-1.5 text-xs font-bold text-hud-text ring-1 ring-hud-border transition-colors hover:bg-hud-panel-hover"
        >
          Retry
        </button>
      </div>
    )
  }
}

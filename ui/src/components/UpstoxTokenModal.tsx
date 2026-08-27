import { CheckCircle2, KeyRound, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState, type ClipboardEvent } from 'react'
import { saveUpstoxToken } from '../lib/api'
import { useBrokerRefreshStore } from '../store/useBrokerRefreshStore'
import { useUpstoxAuthStore } from '../store/useUpstoxAuthStore'

type SaveState = 'idle' | 'validating' | 'success' | 'error'

// Long enough to hold this checkmark on screen before the modal closes
// -- per the sprint's own ask ("trigger a brief green confirmation
// checkmark, close the modal automatically").
const SUCCESS_DISPLAY_MS = 900

/**
 * Auto-opening, auto-saving Upstox Token Dialog -- "Telegram Redesign &
 * Token Modal" sprint (2026-08-27). Opens whenever
 * useUpstoxAuthWatcher.ts's poll of the real GET /api/auth/upstox/status
 * says `needs_token`, from any route (mounted once, globally, in
 * Layout.tsx).
 *
 * A `<textarea>`, not a password input: Upstox access tokens are long
 * JWTs (several hundred characters), and this is explicitly a local,
 * single-operator recovery tool (api/routes/auth.py's own module
 * docstring: "local dashboard recovery") -- masking a string the trader
 * needs to visually confirm pasted correctly would be a real usability
 * regression for zero real security benefit here. Monospace so a
 * garbled/truncated paste is actually visible as such.
 *
 * Auto-save fires on paste, per the sprint's own literal ask -- typing
 * does not auto-submit (nobody hand-types a JWT), but a manual "Save"
 * button is kept as a fallback for the rare case a paste doesn't fire a
 * DOM paste event (some browsers' right-click-paste, some mobile
 * keyboards), so a trader is never stuck with no way to submit.
 *
 * Layout.tsx only renders this component while isTokenModalOpen is
 * true -- mount/unmount IS the open/close transition, so every field
 * below starts fresh via its own useState initializer with no separate
 * "reset on open" effect needed (that would be resetting state in
 * response to a prop change from inside the same component, the exact
 * pattern React's own compiler-safety lint flags; a fresh mount already
 * gets a clean slate for free).
 */
export function UpstoxTokenModal() {
  const closeTokenModal = useUpstoxAuthStore((s) => s.closeTokenModal)
  const triggerBrokerRefresh = useBrokerRefreshStore((s) => s.triggerBrokerRefresh)

  const [value, setValue] = useState('')
  const [state, setState] = useState<SaveState>('idle')
  const [error, setError] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const submit = async (token: string) => {
    const trimmed = token.trim()
    if (!trimmed) return
    setState('validating')
    setError('')
    const result = await saveUpstoxToken(trimmed)
    if (result.status === 'success') {
      setState('success')
      triggerBrokerRefresh()
      window.setTimeout(() => closeTokenModal(), SUCCESS_DISPLAY_MS)
    } else {
      setState('error')
      setError(result.message || 'Invalid Upstox Token')
      textareaRef.current?.focus()
    }
  }

  const handlePaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = e.clipboardData.getData('text')
    if (!pasted.trim()) return
    setValue(pasted)
    void submit(pasted)
  }

  const busy = state === 'validating'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="upstox-token-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div className="w-full max-w-lg rounded-xl border border-hud-border bg-hud-panel p-6 shadow-2xl shadow-black/50">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-horizon-btst/15 text-horizon-btst">
            <KeyRound className="h-5 w-5" />
          </div>
          <div>
            <h2 id="upstox-token-modal-title" className="text-sm font-bold text-hud-text">
              Upstox Session Expired
            </h2>
            <p className="text-xs text-hud-muted">
              Live broker sync is paused until a fresh access token is provided.
            </p>
          </div>
        </div>

        <label
          htmlFor="upstox-token-input"
          className="mt-5 block text-xs font-bold uppercase tracking-wide text-hud-muted"
        >
          Paste Today&apos;s Upstox Access Token
        </label>
        <textarea
          id="upstox-token-input"
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onPaste={handlePaste}
          disabled={busy}
          rows={4}
          spellCheck={false}
          autoComplete="off"
          placeholder="eyJhbGciOi..."
          className={
            'mt-2 w-full resize-none rounded-lg border bg-hud-bg px-3 py-2 font-mono text-xs text-hud-text ' +
            'placeholder:text-hud-muted focus:outline-none focus:ring-2 ' +
            (state === 'error'
              ? 'border-bear focus:ring-bear/50'
              : 'border-hud-border focus:ring-bull/50')
          }
        />

        <div className="mt-3 flex min-h-[28px] items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs">
            {state === 'validating' && (
              <span className="flex items-center gap-1.5 text-horizon-btst">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Validating with Upstox…
              </span>
            )}
            {state === 'success' && (
              <span className="flex items-center gap-1.5 font-bold text-bull">
                <CheckCircle2 className="h-4 w-4" />
                Token saved
              </span>
            )}
            {state === 'error' && (
              <span className="rounded bg-bear/15 px-2 py-1 font-bold text-bear">{error}</span>
            )}
          </div>

          <button
            type="button"
            onClick={() => void submit(value)}
            disabled={busy || !value.trim()}
            className="shrink-0 rounded-lg bg-bull/15 px-3 py-1.5 text-xs font-bold text-bull transition-colors hover:bg-bull/25 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Save Token
          </button>
        </div>

        <p className="mt-3 text-[10px] text-hud-muted">
          Pasting automatically submits the token -- no live orders are ever placed by this
          dashboard; this only restores read-only broker sync.
        </p>
      </div>
    </div>
  )
}

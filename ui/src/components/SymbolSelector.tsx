import { Search } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchSymbols } from '../lib/api'
import type { SymbolMeta } from '../types'

interface SymbolSelectorProps {
  value?: string
  onSelect: (symbol: string) => void
  placeholder?: string
}

/** Searchable F&O symbol combobox -- "Unified Screener & Deep-Dive
 * Interactivity" sprint (2026-08-28). Sourced from the real GET
 * /api/symbols universe (infusion:symbols in Redis, the actual set
 * this pipeline tracks -- not a second hand-maintained list), fetched
 * once per mount rather than polled: the tracked symbol universe
 * doesn't change within a session, matching this app's own established
 * "fetch once, not every poll cycle" precedent for rarely-changing
 * reference data (e.g. useLabScatter's own note on why it does the
 * same). */
export function SymbolSelector({ value, onSelect, placeholder }: SymbolSelectorProps) {
  const [symbols, setSymbols] = useState<SymbolMeta[]>([])
  // React's own documented pattern for resetting state when a prop
  // changes ("Adjusting state when a prop changes", react.dev), same
  // idiom useHistoricalData.ts already uses for its own symbol/interval
  // reset: setState during render, not inside an effect (which would
  // be a real react-hooks(exhaustive-deps)/set-state-in-effect anti-
  // pattern this codebase's own lint already flags elsewhere).
  const [prevValue, setPrevValue] = useState(value)
  const [query, setQuery] = useState(value ?? '')
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  if (value !== prevValue) {
    setPrevValue(value)
    setQuery(value ?? '')
  }

  useEffect(() => {
    void fetchSymbols().then(setSymbols)
  }, [])

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const needle = query.trim().toUpperCase()
  const matches = (
    needle.length === 0 ? symbols : symbols.filter((s) => s.symbol.includes(needle))
  ).slice(0, 20)

  const select = (symbol: string) => {
    onSelect(symbol)
    setQuery(symbol)
    setIsOpen(false)
  }

  return (
    <div ref={containerRef} className="relative w-full max-w-xs">
      <div className="flex items-center gap-2 rounded-lg border border-hud-border bg-hud-bg px-3 py-2 focus-within:ring-1 focus-within:ring-bull/50">
        <Search className="h-3.5 w-3.5 shrink-0 text-hud-muted" />
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setIsOpen(true)
          }}
          onFocus={() => setIsOpen(true)}
          placeholder={placeholder || 'Search F&O symbol…'}
          aria-label="F&O symbol search"
          className="w-full bg-transparent font-mono text-xs uppercase text-hud-text placeholder:text-hud-muted placeholder:normal-case focus:outline-none"
        />
      </div>
      {isOpen && matches.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-hud-border bg-hud-panel shadow-xl">
          {matches.map((s) => (
            <li key={s.symbol}>
              <button
                type="button"
                onClick={() => select(s.symbol)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-mono text-hud-text transition-colors hover:bg-hud-panel-hover"
              >
                <span className="font-bold">{s.symbol}</span>
                {s.sector_id && (
                  <span className="text-[10px] uppercase tracking-wide text-hud-muted">
                    {s.sector_id}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

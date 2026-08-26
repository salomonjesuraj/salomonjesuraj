import { useEffect, useRef, useState } from 'react'

interface PollingResult<T> {
  data: T | null
  error: Error | null
  loading: boolean
}

/** Polls `fetcher` every `intervalMs`, starting immediately. Stops on
 * unmount. A fetch failure keeps the last good `data` on screen (never
 * blanks a working card because of one dropped poll) and surfaces the
 * error for callers that want to show a degraded state. */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: React.DependencyList,
): PollingResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    const tick = async () => {
      try {
        const result = await fetcherRef.current()
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)))
      } finally {
        if (!cancelled) setLoading(false)
      }
      if (!cancelled) timer = window.setTimeout(tick, intervalMs)
    }

    void tick()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, error, loading }
}

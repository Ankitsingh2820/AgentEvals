import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

interface Loaded<T> {
  key: string
  data: T | null
  error: string | null
}

/**
 * Fetch `path` (null = don't fetch). On refetch the previous data is kept, so charts hold
 * their last render (dimmed via `loading`) instead of flashing a skeleton. Optional
 * `pollMs` refetches while `shouldPoll(data)` is true (e.g. an experiment still running).
 */
export function useApi<T>(
  path: string | null,
  opts: { pollMs?: number; shouldPoll?: (data: T) => boolean } = {},
) {
  const [tick, setTick] = useState(0)
  const [loaded, setLoaded] = useState<Loaded<T>>({ key: '', data: null, error: null })
  const optsRef = useRef(opts)
  useEffect(() => {
    optsRef.current = opts
  })

  const reload = useCallback(() => setTick((t) => t + 1), [])
  const key = path === null ? '' : `${path}#${tick}`

  useEffect(() => {
    if (path === null) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    api
      .get<T>(path)
      .then((d) => {
        if (cancelled) return
        setLoaded({ key: `${path}#${tick}`, data: d, error: null })
        const { pollMs, shouldPoll } = optsRef.current
        if (pollMs && shouldPoll?.(d)) timer = setTimeout(reload, pollMs)
      })
      .catch((e: Error) => {
        if (!cancelled) setLoaded((prev) => ({ key: `${path}#${tick}`, data: prev.data, error: e.message }))
      })
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [path, tick, reload])

  return {
    data: loaded.data,
    error: loaded.error,
    loading: path !== null && loaded.key !== key,
    reload,
  }
}

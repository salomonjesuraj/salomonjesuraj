import { useState } from 'react'
import { SUCCESS_PING_DATA_URI, WARNING_CHIME_DATA_URI } from '../lib/alertSounds'

export interface UseAudioAlertResult {
  playSuccessPing: () => void
  playWarningChime: () => void
}

/**
 * "Omnipresent Alert Engine" sprint (2026-08-27) -- HTML5 Audio, two
 * distinct real generated tones (see lib/alertSounds.ts). Each Audio
 * element is created once and reused (`currentTime = 0` before every
 * replay) rather than a fresh `new Audio()` per call, so rapid repeat
 * triggers don't pile up overlapping decode work.
 *
 * Browsers block audio.play() before the user has interacted with the
 * page at all (autoplay policy) -- that rejection is caught and
 * swallowed here, never thrown into a component's render path. The
 * first alert on a session where the trader hasn't clicked anything
 * yet may silently not play; every alert after any click/keypress
 * will. This is a real browser constraint, not a bug in this hook.
 *
 * Lazy-initialized via useState's own lazy-initializer form (the value
 * itself is never read for rendering, only its stable identity is kept)
 * rather than the more obvious `if (!ref.current) ref.current = ...`
 * ref pattern -- that reads/writes a ref during render, which React's
 * own stricter compiler-safety lint (react/refs) flags even though it
 * works today; this is the React-docs-endorsed way to lazily construct
 * a mutable singleton (a real audio element, here) exactly once.
 */
export function useAudioAlert(): UseAudioAlertResult {
  const [successAudio] = useState(() => new Audio(SUCCESS_PING_DATA_URI))
  const [warningAudio] = useState(() => new Audio(WARNING_CHIME_DATA_URI))

  const play = (audio: HTMLAudioElement) => {
    audio.currentTime = 0
    void audio.play().catch(() => {
      // Autoplay-policy rejection or a mid-flight pause() -- never
      // surface this as an app error, an alert sound failing to play
      // isn't worth interrupting the trader over.
    })
  }

  return {
    playSuccessPing: () => play(successAudio),
    playWarningChime: () => play(warningAudio),
  }
}

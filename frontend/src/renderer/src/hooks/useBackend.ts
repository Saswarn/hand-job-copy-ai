import { createContext, useContext } from 'react'
import type { ClientMessage, ScanState, ServerMessage } from '../common/api'

export type FrameListener = (jpeg: Blob) => void

export type Backend = {
  connected: boolean
  /** Latest UV state from the backend, null until the first "uv" message. */
  uv: boolean[] | null
  /** Latest scan state, null until the backend sends one. */
  scan: ScanState | null
  /** Latest "error" message text, null if none yet. */
  error: string | null
  send: (msg: ClientMessage) => boolean
  /** Live JPEG frames go to listeners, never into React state (30 fps). Returns unsubscribe. */
  subscribeFrames: (listener: FrameListener) => () => void
  subscribeMessages: (listener: (message: ServerMessage) => void) => () => void
}

export const BackendContext = createContext<Backend | null>(null)

/** The app-wide backend connection. Must be used under <BackendProvider>. */
export function useBackend(): Backend {
  const backend = useContext(BackendContext)
  if (!backend) throw new Error('useBackend must be used inside <BackendProvider>')
  return backend
}

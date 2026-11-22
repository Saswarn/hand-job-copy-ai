// Backend contract, mirrored by hand from backend/app/schemas/ws.py and backend/app/core/config.py.
// Source of truth: systems/api.md. Change both sides (and api.md) in the same PR.

export const BACKEND_HTTP_URL = 'http://localhost:8000'
export const BACKEND_WS_URL = 'ws://localhost:8000/ws'

/** UV channel index 0-2, in the order of uv_gpio. */
export type UvChannel = 0 | 1 | 2

// frontend -> backend (text)
export type ClientMessage =
  | { type: 'uv_set'; channel: UvChannel; on: boolean }
  | { type: 'scan_start' }
  | { type: 'engine_scan_start' | 'engine_scan_cancel' }
  | { type: 'binary_view'; on: boolean }

// backend -> frontend (text)
export type UvState = { type: 'uv'; channels: boolean[] }
export type ScanState = {
  type: 'scan'
  state: 'idle' | 'scanning' | 'done'
  remaining_s: number | null
  result: 'clean' | 'dirt' | null
}
export type ErrorMessage = { type: 'error'; message: string }
export type Side = 'palm' | 'back'
export type EnginePreview = {
  type: 'engine_preview'
  hands_found: number
  side: Side | 'unsure' | null
  guidance: string
  timings_ms: Record<string, number>
}
export type EngineCapture = {
  type: 'engine_capture'
  status: 'ok' | 'NO_HAND' | 'ONE_HAND'
  side: Side
  hands: { hand: 'left' | 'right'; score: number }[]
  warnings: string[]
  timings_ms: Record<string, number>
}
export type EngineResult = {
  type: 'engine_result'
  status: 'ok' | 'incomplete'
  S: number | null
  S_worst: number | null
  worst_view: string | null
  missing_views: string[]
  reason: 'timeout' | null
}
export type ServerMessage =
  UvState | ScanState | ErrorMessage | EnginePreview | EngineCapture | EngineResult

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null
const isSide = (v: unknown): v is Side => v === 'palm' || v === 'back'
const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isString = (v: unknown): v is string => typeof v === 'string'
const isStrings = (v: unknown): v is string[] => Array.isArray(v) && v.every(isString)
const isTimings = (v: unknown): v is Record<string, number> =>
  isObject(v) && !Array.isArray(v) && Object.values(v).every(isNumber)
const isHand = (v: unknown): v is EngineCapture['hands'][number] =>
  isObject(v) && (v.hand === 'left' || v.hand === 'right') && isNumber(v.score)

/** Parses one text message from /ws. Returns null for anything that is not a known, well-formed message. */
export function parseServerMessage(text: string): ServerMessage | null {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return null
  }
  if (!isObject(data)) return null
  switch (data.type) {
    case 'uv':
      return Array.isArray(data.channels) && data.channels.every((c) => typeof c === 'boolean')
        ? { type: 'uv', channels: data.channels }
        : null
    case 'scan':
      return (data.state === 'idle' || data.state === 'scanning' || data.state === 'done') &&
        (data.remaining_s === null || typeof data.remaining_s === 'number') &&
        (data.result === null || data.result === 'clean' || data.result === 'dirt')
        ? { type: 'scan', state: data.state, remaining_s: data.remaining_s, result: data.result }
        : null
    case 'error':
      return typeof data.message === 'string' ? { type: 'error', message: data.message } : null
    case 'engine_preview':
      return isNumber(data.hands_found) &&
        (isSide(data.side) || data.side === null || data.side === 'unsure') &&
        isString(data.guidance) &&
        isTimings(data.timings_ms)
        ? {
            type: 'engine_preview',
            hands_found: data.hands_found,
            side: data.side,
            guidance: data.guidance,
            timings_ms: data.timings_ms
          }
        : null
    case 'engine_capture':
      return (data.status === 'ok' || data.status === 'NO_HAND' || data.status === 'ONE_HAND') &&
        isSide(data.side) &&
        Array.isArray(data.hands) &&
        data.hands.every(isHand) &&
        isStrings(data.warnings) &&
        isTimings(data.timings_ms)
        ? {
            type: 'engine_capture',
            status: data.status,
            side: data.side,
            hands: data.hands,
            warnings: data.warnings,
            timings_ms: data.timings_ms
          }
        : null
    case 'engine_result':
      return (data.status === 'ok' || data.status === 'incomplete') &&
        (isNumber(data.S) || data.S === null) &&
        (isNumber(data.S_worst) || data.S_worst === null) &&
        (isString(data.worst_view) || data.worst_view === null) &&
        isStrings(data.missing_views) &&
        (data.reason === 'timeout' || data.reason === null)
        ? {
            type: 'engine_result',
            status: data.status,
            S: data.S,
            S_worst: data.S_worst,
            worst_view: data.worst_view,
            missing_views: data.missing_views,
            reason: data.reason
          }
        : null
    default:
      return null
  }
}

// GET /config -> AppConfig · PUT /config <- AppConfig (204, or 422 on bad values; 501 until saving lands)
export type AppConfig = {
  uv_gpio: [number, number, number]
  camera: {
    device: string
    capture: { width: number; height: number; fps: number }
    exposure: number
    gain: number
    white_balance: number
    zoom: number
    crop: [number, number, number, number] // [x1, y1, x2, y2], 0-1 of the camera frame
  }
  binary_threshold: number
  stream: { min_width: number; quality: number }
  scan_seconds: number
  dirt_conf: number
  dirt_min_frames: number
}

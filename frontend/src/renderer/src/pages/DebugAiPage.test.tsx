import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import type { ServerMessage } from '../common/api'
import { BackendContext } from '../hooks/useBackend'
import DebugAiPage from './DebugAiPage'

vi.mock('../components/LiveView', () => ({ default: () => <canvas /> }))
afterEach(cleanup)

function openDebug(): {
  send: ReturnType<typeof vi.fn>
  emit: (message: ServerMessage) => void
  unmount: () => void
} {
  const send = vi.fn(() => true)
  let listener: ((message: ServerMessage) => void) | undefined
  const { unmount } = render(
    <BackendContext.Provider
      value={{
        connected: true,
        uv: [false, false, false],
        scan: null,
        error: null,
        send,
        subscribeFrames: () => () => undefined,
        subscribeMessages: (callback) => {
          listener = callback
          return () => {
            listener = undefined
          }
        }
      }}
    >
      <MemoryRouter>
        <DebugAiPage />
      </MemoryRouter>
    </BackendContext.Provider>
  )
  return { send, emit: (message) => act(() => listener?.(message)), unmount }
}

it('starts on demand and displays guidance, capture warnings and final score', () => {
  const { send, emit, unmount } = openDebug()
  expect(send).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'เริ่มสแกน' }))
  expect(send).toHaveBeenCalledWith({ type: 'engine_scan_start' })
  emit({
    type: 'engine_preview',
    side: 'palm',
    hands_found: 2,
    guidance: 'HOLD_STILL',
    timings_ms: { total: 2 },
    focus: 123.4
  })
  expect(screen.getByText('ถือนิ่ง ๆ')).toBeTruthy()
  expect(screen.getByText('ความคมชัด (focus) 123 · หมุนวงแหวนโฟกัสให้ค่านี้สูงสุด')).toBeTruthy()
  emit({
    type: 'engine_capture',
    side: 'palm',
    status: 'ok',
    hands: [{ hand: 'left', score: 75 }],
    warnings: ['MASK_FALLBACK'],
    timings_ms: {}
  })
  expect(screen.getByText('ซ้าย 75.00')).toBeTruthy()
  expect(screen.getByText('คำเตือน MASK_FALLBACK')).toBeTruthy()
  emit({
    type: 'engine_result',
    status: 'ok',
    S: 80,
    S_worst: 75,
    worst_view: 'left_palm',
    missing_views: [],
    reason: null
  })
  expect(screen.getByText('คะแนนรวม 80.00')).toBeTruthy()
  unmount()
  expect(send).toHaveBeenCalledTimes(1)
})

it('requests cancellation when leaving during a scan', () => {
  const { send, unmount } = openDebug()
  fireEvent.click(screen.getByRole('button', { name: 'เริ่มสแกน' }))
  unmount()
  expect(send).toHaveBeenLastCalledWith({ type: 'engine_scan_cancel' })
})

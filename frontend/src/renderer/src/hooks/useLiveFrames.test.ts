import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type FrameListener, useBackend } from './useBackend'
import { useLiveFrames } from './useLiveFrames'

vi.mock('./useBackend', () => ({ useBackend: vi.fn() }))

/** createImageBitmap stub whose decodes finish only when the test says so. */
function controllableDecodes(): Array<(width: number) => ImageBitmap> {
  const resolvers: Array<(width: number) => ImageBitmap> = []
  vi.stubGlobal(
    'createImageBitmap',
    () =>
      new Promise<ImageBitmap>((resolve) => {
        resolvers.push((width) => {
          const bitmap = { width, height: 10, close: vi.fn() } as unknown as ImageBitmap
          resolve(bitmap)
          return bitmap
        })
      })
  )
  return resolvers
}

describe('useLiveFrames', () => {
  const drawImage = vi.fn()
  const unsubscribe = vi.fn()
  let listener: FrameListener | undefined

  beforeEach(() => {
    listener = undefined
    vi.mocked(useBackend).mockReturnValue({
      connected: true,
      uv: null,
      scan: null,
      error: null,
      send: vi.fn(),
      subscribeMessages: () => unsubscribe,
      subscribeFrames: (l) => {
        listener = l
        return unsubscribe
      }
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      drawImage
    } as unknown as CanvasRenderingContext2D)
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    drawImage.mockClear()
    unsubscribe.mockClear()
  })

  function setup(): { canvas: HTMLCanvasElement; unmount: () => void; push: FrameListener } {
    const canvas = document.createElement('canvas')
    const { unmount } = renderHook(() => useLiveFrames({ current: canvas }))
    const push = listener
    if (!push) throw new Error('not subscribed to frames')
    return { canvas, unmount, push }
  }

  it('draws the newest frame and drops one that finishes decoding late', async () => {
    const decodes = controllableDecodes()
    const { canvas, push } = setup()
    push(new Blob())
    push(new Blob())

    const newer = await act(async () => decodes[1]?.(20))
    const older = await act(async () => decodes[0]?.(10))

    expect(drawImage).toHaveBeenCalledExactlyOnceWith(newer, 0, 0)
    expect(canvas.width).toBe(20) // canvas follows the frame size
    expect(older?.close).toHaveBeenCalled()
    expect(newer?.close).toHaveBeenCalled() // closed right after drawing
  })

  it('unsubscribes on unmount', () => {
    setup().unmount()
    expect(unsubscribe).toHaveBeenCalled()
  })
})

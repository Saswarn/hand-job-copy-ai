import { describe, expect, it } from 'vitest'
import { parseServerMessage } from './api'

describe('parseServerMessage', () => {
  it('accepts engine results and rejects invalid capture scores', () => {
    const result = {
      type: 'engine_result',
      status: 'ok',
      S: 80,
      S_worst: 75,
      worst_view: 'left_palm',
      missing_views: [],
      reason: null
    }
    expect(parseServerMessage(JSON.stringify(result))).toEqual(result)
    expect(
      parseServerMessage(
        '{"type":"engine_capture","status":"ok","side":"palm","hands":[{"hand":"left","score":"80"}],"warnings":[],"timings_ms":{}}'
      )
    ).toBeNull()
  })

  it('accepts every message in systems/api.md', () => {
    expect(parseServerMessage('{"type":"uv","channels":[false,true,false]}')).toEqual({
      type: 'uv',
      channels: [false, true, false]
    })
    expect(
      parseServerMessage('{"type":"scan","state":"scanning","remaining_s":3.2,"result":null}')
    ).toEqual({ type: 'scan', state: 'scanning', remaining_s: 3.2, result: null })
    expect(
      parseServerMessage('{"type":"scan","state":"done","remaining_s":null,"result":"dirt"}')
    ).toEqual({ type: 'scan', state: 'done', remaining_s: null, result: 'dirt' })
    expect(parseServerMessage('{"type":"error","message":"bad"}')).toEqual({
      type: 'error',
      message: 'bad'
    })
  })

  it.each([
    'not json',
    'null',
    '[]',
    '{"type":"unknown"}',
    '{"type":"uv","channels":[1,0,0]}',
    '{"type":"scan","state":"paused","remaining_s":null,"result":null}',
    '{"type":"scan","state":"done","remaining_s":null,"result":"maybe"}',
    '{"type":"error"}'
  ])('rejects %s', (text) => {
    expect(parseServerMessage(text)).toBeNull()
  })
})

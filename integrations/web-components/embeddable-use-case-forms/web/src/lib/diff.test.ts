import { describe, expect, it } from 'vitest'

import { detectChanges } from './diff'

describe('detectChanges', () => {
  it('reports changed scalar values with old and new', () => {
    const diff = detectChanges({ q1: 'local' }, { q1: 'upstream' })
    expect(diff.get('q1')).toEqual({ oldValue: 'local', newValue: 'upstream' })
  })

  it('ignores unchanged values', () => {
    expect(detectChanges({ q1: 'same' }, { q1: 'same' }).size).toBe(0)
  })

  it('compares arrays order-insensitively', () => {
    expect(detectChanges({ q1: ['b', 'a'] }, { q1: ['a', 'b'] }).size).toBe(0)
    const diff = detectChanges({ q1: ['a'] }, { q1: ['a', 'b'] })
    expect(diff.get('q1')).toEqual({ oldValue: ['a'], newValue: ['a', 'b'] })
  })

  it('filters non-string elements from the saved array before comparing', () => {
    expect(detectChanges({ q1: ['a', 42, 'b'] }, { q1: ['a', 'b'] }).size).toBe(0)
  })

  it('treats a type mismatch as changed', () => {
    const diff = detectChanges({ q1: 'scalar' }, { q1: ['array'] })
    expect(diff.get('q1')).toEqual({ oldValue: 'scalar', newValue: ['array'] })
  })

  it('skips empty upstream values and questions absent upstream', () => {
    const diff = detectChanges(
      { q1: 'local-only', q2: 'kept' },
      { q2: null, q3: '', q4: [] },
    )
    expect(diff.size).toBe(0)
  })

  it('renders undefined local values as empty string', () => {
    const diff = detectChanges({}, { q1: 'new answer' })
    expect(diff.get('q1')).toEqual({ oldValue: '', newValue: 'new answer' })
  })
})

import { describe, expect, it } from 'vitest'

import { attributeKey } from './attribute-key'

describe('attributeKey', () => {
  it('survives the circular non-attribute props r2wc passes down', () => {
    const container: Record<string, unknown> = { tagName: 'DIV' }
    container.self = container
    expect(() => attributeKey({ tenant: 'acme', container })).not.toThrow()
  })

  it('ignores non-attribute props entirely', () => {
    const container: Record<string, unknown> = {}
    container.self = container
    expect(attributeKey({ tenant: 'acme', container })).toBe(attributeKey({ tenant: 'acme' }))
  })

  it('is stable across key order and changes with any attribute value', () => {
    expect(attributeKey({ a: '1', b: '2' })).toBe(attributeKey({ b: '2', a: '1' }))
    expect(attributeKey({ a: '1' })).not.toBe(attributeKey({ a: '2' }))
    expect(attributeKey({ allowSubmit: true })).not.toBe(attributeKey({ allowSubmit: false }))
  })
})

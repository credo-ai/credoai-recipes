import { describe, expect, it } from 'vitest'

import { parseJsonStringArray } from './json-config'

describe('parseJsonStringArray', () => {
  it('parses a valid JSON array of strings', () => {
    expect(parseJsonStringArray('["a","b"]')).toEqual(['a', 'b'])
  })

  it('degrades to an empty list rather than throwing', () => {
    expect(parseJsonStringArray(undefined)).toEqual([])
    expect(parseJsonStringArray(null)).toEqual([])
    expect(parseJsonStringArray('')).toEqual([])
    expect(parseJsonStringArray('not json')).toEqual([])
    expect(parseJsonStringArray('{"a":1}')).toEqual([])
  })

  it('filters out non-string elements', () => {
    expect(parseJsonStringArray('["a",1,null,"b"]')).toEqual(['a', 'b'])
  })
})

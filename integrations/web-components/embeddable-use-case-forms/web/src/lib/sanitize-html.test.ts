// @vitest-environment jsdom
//
// DOMPurify needs a real `window`/`document` to sanitize against. The rest
// of this suite runs under vitest's default `node` environment (these are
// pure functions with no DOM dependency) — this docblock scopes `jsdom` to
// only this file rather than changing the global test environment.
import { describe, expect, it } from 'vitest'

import { sanitizeFooterHtml } from './sanitize-html'

describe('sanitizeFooterHtml', () => {
  it('passes plain text through unchanged', () => {
    expect(sanitizeFooterHtml('Powered by Credo AI')).toBe('Powered by Credo AI')
  })

  it('keeps allowed inline tags', () => {
    expect(sanitizeFooterHtml('<strong>Bold</strong> text')).toBe('<strong>Bold</strong> text')
  })

  it('strips disallowed tags and attributes', () => {
    expect(sanitizeFooterHtml('<script>alert(1)</script>hi')).toBe('hi')
    expect(sanitizeFooterHtml('<img src=x onerror=alert(1)>hi')).toBe('hi')
  })

  it('forces target and rel on a link even if the input tries to set different values', () => {
    const result = sanitizeFooterHtml('<a href="https://credo.ai" target="_self" rel="opener">Credo AI</a>')
    expect(result).toContain('target="_blank"')
    expect(result).toContain('rel="noopener noreferrer"')
    expect(result).toContain('href="https://credo.ai"')
  })
})

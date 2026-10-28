// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'

// Imports register the custom elements as a side effect — the same thing a
// host page's <script src> does.
import './use-case-edit-form'
import './use-case-intake-form'
import './use-case-preview'

const ELEMENTS = [
  'credo-ai-use-case-intake-form',
  'credo-ai-use-case-edit-form',
  'credo-ai-use-case-preview',
] as const

// React 18 mounts asynchronously; let the microtask queue and one macrotask
// drain before asserting on the shadow tree.
async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

function mount(tag: string, attributes: Record<string, string> = {}): HTMLElement {
  const element = document.createElement(tag)
  for (const [name, value] of Object.entries(attributes)) {
    element.setAttribute(name, value)
  }
  document.body.appendChild(element)
  return element
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('custom element registration', () => {
  it.each(ELEMENTS)('registers %s', (tag) => {
    expect(customElements.get(tag)).toBeTypeOf('function')
  })

  it.each(ELEMENTS)('%s attaches an open shadow root', async (tag) => {
    const element = mount(tag)
    await flush()
    expect(element.shadowRoot).not.toBeNull()
  })

  it.each(ELEMENTS)('%s injects its stylesheet exactly once', async (tag) => {
    const element = mount(tag)
    await flush()
    // Re-connecting must not stack a second <style> in the shadow root.
    element.remove()
    document.body.appendChild(element)
    await flush()
    expect(element.shadowRoot?.querySelectorAll('style[data-credo-styles]')).toHaveLength(1)
  })
})

describe('missing configuration', () => {
  it.each(ELEMENTS)('%s renders a notice instead of calling the API', async (tag) => {
    const element = mount(tag)
    await flush()
    expect(element.shadowRoot?.textContent).toMatch(/not configured|missing credentials/i)
  })

  it('tells the host which credential attributes are missing', async () => {
    const element = mount('credo-ai-use-case-edit-form', {
      tenant: 'acme',
      'use-case-id': 'uc1',
      user: 'someone@example.com',
    })
    await flush()
    expect(element.shadowRoot?.textContent).toContain('token-endpoint')
  })

  it('names the missing use-case-id on the edit form once credentials are present', async () => {
    const element = mount('credo-ai-use-case-edit-form', {
      tenant: 'acme',
      user: 'someone@example.com',
      'token-endpoint': 'http://localhost:8000/api/token',
    })
    await flush()
    expect(element.shadowRoot?.textContent).toContain('use-case-id')
  })

  it('names the missing use-case-id on the preview once credentials are present', async () => {
    const element = mount('credo-ai-use-case-preview', {
      tenant: 'acme',
      'token-endpoint': 'http://localhost:8000/api/token',
    })
    await flush()
    expect(element.shadowRoot?.textContent).toContain('use-case-id')
  })
})

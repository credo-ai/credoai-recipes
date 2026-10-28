import r2wc from '@r2wc/react-to-web-component'
import type { ComponentType } from 'react'

import styles from '../index.css?inline'

// Turns a React component into a real custom element:
// `@r2wc/react-to-web-component` handles the attribute -> prop plumbing, and a
// subclass injects the compiled stylesheet into the shadow root.
//
// Using r2wc rather than hand-rolling `observedAttributes` is deliberate: it
// gives the kebab-attribute -> camelCase-prop mapping, the boolean coercion,
// and the re-render-on-attribute-change behaviour r2wc's standard semantics,
// matching Credo AI's hosted web components.

export type PropTypes<P> = Record<keyof P, 'string' | 'boolean' | 'number'>

interface ShadowElement extends HTMLElement {
  shadowRoot: ShadowRoot | null
  connectedCallback(): void
}

// Tailwind 3.4's preflight already targets `html, :host`, so the compiled
// stylesheet applies inside a shadow root verbatim — no `:root` rewriting.
function injectStyles(root: ShadowRoot | null): void {
  if (!root || root.querySelector('style[data-credo-styles]')) {
    return
  }
  const style = document.createElement('style')
  style.setAttribute('data-credo-styles', 'true')
  style.textContent = styles
  // Prepended so component markup can still override it by specificity.
  root.prepend(style)
}

export function defineElement<P extends object>(
  name: string,
  Component: ComponentType<P>,
  props: PropTypes<P>,
): void {
  // Re-registering a custom element name throws. A host page that loads two
  // bundles sharing a dependency should get a no-op, not a hard failure.
  if (customElements.get(name)) {
    return
  }
  const Base = r2wc(Component, { shadow: 'open', props }) as unknown as new () => ShadowElement

  customElements.define(
    name,
    class extends Base {
      connectedCallback(): void {
        super.connectedCallback()
        injectStyles(this.shadowRoot)
      }
    },
  )
}

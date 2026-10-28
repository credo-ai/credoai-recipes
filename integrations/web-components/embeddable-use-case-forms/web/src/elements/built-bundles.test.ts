// @vitest-environment jsdom
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { ELEMENTS } from '../../elements.config.js'

// Evaluates the BUILT bundles, not the source.
//
// This exists because of a real bug: React reads `process.env.NODE_ENV`, and
// Vite substitutes that automatically for app builds but NOT in library mode —
// which is what every element build uses. The bundle threw `ReferenceError:
// process is not defined` at load and registered nothing. Nothing else in the
// suite could catch it: the other tests import source, and `vite dev` shims
// the Node globals away, so it only appeared when loading a built file in a
// real browser.
//
// Skips when dist/ is absent so `npm test` works on a clean checkout; run
// `npm run build` first to exercise it.
// Resolved from the package root, not `import.meta.url`: under the jsdom
// environment this file declares, `import.meta.url` is not a file:// URL and
// a relative resolve silently collapses to an absolute path that never exists
// — which would make this suite skip itself forever instead of failing.
const bundlePath = (name: string) => resolve(process.cwd(), 'dist', `${name}.js`)
const built: string[] = ELEMENTS.filter((name) => existsSync(bundlePath(name)))

describe.skipIf(built.length === 0)('built bundles', () => {
  it('has a bundle for every element', () => {
    expect(built).toEqual(ELEMENTS)
  })

  it.each(built)('%s evaluates and registers its element in a browser-like global', (name) => {
    const code = readFileSync(bundlePath(name), 'utf8')
    const tag = `credo-ai-${name}`
    expect(customElements.get(tag)).toBeUndefined()

    // The whole point: a browser has no `process` and no `global`. Node and
    // jsdom both provide them, so evaluating with them in place cannot
    // reproduce the load-time crash this suite exists to catch — the bundle
    // has to be evaluated with them removed.
    const realProcess = globalThis.process
    const realGlobal = (globalThis as { global?: unknown }).global
    // @ts-expect-error deliberately simulating a browser global
    delete globalThis.process
    // @ts-expect-error deliberately simulating a browser global
    delete globalThis.global
    try {
      expect(() => new Function(code).call(globalThis)).not.toThrow()
    } finally {
      globalThis.process = realProcess
      ;(globalThis as { global?: unknown }).global = realGlobal
    }

    expect(customElements.get(tag)).toBeTypeOf('function')
  })
})

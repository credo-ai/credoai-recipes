/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Each component ships as ONE self-contained IIFE file (`dist/<name>.js`),
// mirroring the layout of Credo AI's hosted web components — a host page adds a
// single <script src> and gets a working custom element, with no module
// graph, import map, or sibling chunk to host alongside it.
//
// Rollup can only emit IIFE for a single entry, so the build runs once per
// component with WC_ENTRY set; `npm run build` loops over all four. Without
// WC_ENTRY (i.e. `vite dev`) the config falls back to the multi-page dev
// server that serves registry/*.html.
const entry = process.env.WC_ENTRY

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  // Two Node globals that don't exist in a browser and would otherwise reach
  // the bundle. Both are load-time crashes, not degraded behaviour, and
  // neither reproduces in `vite dev` — the dev server shims them, so these are
  // only observable by loading a BUILT bundle in a real browser.
  //
  // - `global`: @credo-ai/sdk's browser bundle references it inside a
  //   case-conversion dependency (`ReferenceError: global is not defined`).
  // - `process.env.NODE_ENV`: React reads it. Vite substitutes this
  //   automatically for app builds but NOT in library mode, which is what
  //   every element build uses (`ReferenceError: process is not defined`).
  define: {
    global: 'globalThis',
    'process.env.NODE_ENV': JSON.stringify('production'),
  },
  // Vite 8's dev dependency optimizer does not apply `define` to pre-bundled
  // deps, so @credo-ai/sdk's `global` reference crashes `vite dev` too.
  optimizeDeps: {
    rolldownOptions: {
      transform: { define: { global: 'globalThis' } },
    },
  },
  build: entry
    ? {
        // Each component build appends to dist/ rather than wiping the ones
        // built before it.
        emptyOutDir: false,
        // public/ holds the dev harness's config.js, which carries real tenant
        // values. dist/ is the publishable artifact — it must contain the
        // component bundles and nothing else.
        copyPublicDir: false,
        lib: {
          entry: fileURLToPath(new URL(`src/elements/${entry}.tsx`, import.meta.url)),
          formats: ['iife'],
          // Not referenced — the entry registers a custom element as a side
          // effect and exports nothing — but Vite requires a name for IIFE.
          name: 'CredoAIWebComponents',
          fileName: () => `${entry}.js`,
        },
      }
    : undefined,
  test: {
    // Default to `node`; the few suites that need a DOM (DOMPurify, the custom
    // elements themselves) opt into jsdom with a per-file @vitest-environment.
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
  },
})

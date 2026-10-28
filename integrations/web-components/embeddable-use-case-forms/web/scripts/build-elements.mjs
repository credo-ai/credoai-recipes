// Builds every element into its own self-contained dist/<name>.js.
// One Vite build per element: rollup only emits IIFE for a single entry, and
// a single file per component is the whole point of the output format (see
// vite.config.ts).
import { rm } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { build } from 'vite'

import { ELEMENTS } from '../elements.config.js'

const distDir = fileURLToPath(new URL('../dist', import.meta.url))
await rm(distDir, { recursive: true, force: true })

for (const element of ELEMENTS) {
  process.env.WC_ENTRY = element
  await build()
}

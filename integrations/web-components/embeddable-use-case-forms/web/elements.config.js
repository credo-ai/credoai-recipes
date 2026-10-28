// The element registry: one entry per custom element this recipe ships.
// Shared by vite.config.ts (which entry to build) and scripts/build-elements.mjs
// (which entries to loop over). Adding a component means adding it here, an
// entry point in src/elements/, and a host page in registry/.
export const ELEMENTS = [
  'use-case-intake-form',
  'use-case-edit-form',
  'use-case-preview',
]

// @types/react@18.3.31 doesn't type the native `inert` attribute yet. Remove
// this augmentation once/if an upgraded @types/react adds it natively.
//
// The `export {}` is required — without it, this file has no top-level
// import/export and TS treats it as a script, so `declare module 'react'`
// would replace the real module's types instead of merging with them.
export {}

declare module 'react' {
  // T is unused here but must stay to match the real interface's generic
  // parameter list, or this augmentation won't merge with it.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface HTMLAttributes<T> {
    inert?: boolean
  }
}

import { useCallback, useRef } from 'react'

// Host-page event plumbing. Events are dispatched from a ref'd node inside the
// shadow root with `composed: true` so they cross the shadow boundary and
// reach listeners bound to the custom element itself.
export function useHostEvents<E extends string>() {
  const elementRef = useRef<HTMLDivElement | null>(null)

  const emit = useCallback((event: E, detail: unknown) => {
    elementRef.current?.dispatchEvent(
      new CustomEvent(event, { detail, bubbles: true, composed: true }),
    )
  }, [])

  return { elementRef, emit }
}

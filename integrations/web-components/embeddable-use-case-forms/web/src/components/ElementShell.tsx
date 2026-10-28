import type { ReactNode } from 'react'

// Every element renders through this: a shadow-DOM root node that host events
// are dispatched from, plus the two states an element can be in before it has
// anything to show — misconfigured, or missing credentials.

export function ElementRoot({
  elementRef,
  children,
}: {
  elementRef: React.Ref<HTMLDivElement>
  children: ReactNode
}) {
  return (
    <div ref={elementRef} className="text-gray-900">
      {children}
    </div>
  )
}

export function ElementNotice({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-5 py-8 shadow-sm">
      <p className="text-sm font-semibold text-gray-900">{title}</p>
      <p className="mt-1 text-sm text-gray-600">{body}</p>
    </div>
  )
}

export function ElementLoading({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 px-5 py-8 text-sm text-gray-500">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600"
        aria-hidden="true"
      />
      {label}
    </div>
  )
}

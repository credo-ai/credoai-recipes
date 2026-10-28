import type { ReactNode } from 'react'

// Shared chrome for the three use-case forms: a titled header with actions,
// and the card/section primitives the form bodies are built from. Styling
// follows the transparency card's (CardToolbar/SectionPanel) so all four
// components read as one family.

export function FormHeader({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-gray-200 px-5 py-3.5">
      <div className="min-w-0">
        <p className="text-[15px] font-bold text-gray-900">{title}</p>
        <p className="mt-0.5 text-[13px] text-gray-500">{subtitle}</p>
      </div>
      {children ? <div className="flex flex-shrink-0 items-center gap-2">{children}</div> : null}
    </div>
  )
}

export function PrimaryButton({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded-lg bg-purple-800 px-4 py-1.5 text-[13px] font-bold text-white hover:bg-purple-900 disabled:opacity-50"
    >
      {children}
    </button>
  )
}

export function SecondaryButton({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-[13px] font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
    >
      {children}
    </button>
  )
}

export function FormCard({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
      {children}
    </div>
  )
}

export function FormSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-gray-200 px-5 py-4 last:border-b-0">
      <h3 className="mb-3 text-[13px] font-bold uppercase tracking-wide text-gray-500">
        {title}
      </h3>
      {children}
    </section>
  )
}

export function FormBanner({
  tone,
  title,
  body,
}: {
  tone: 'warning' | 'error' | 'success'
  title: string
  body: string
}) {
  const toneClass = {
    warning: 'border-amber-300 bg-amber-50 text-amber-900',
    error: 'border-red-300 bg-red-50 text-red-800',
    success: 'border-green-300 bg-green-50 text-green-900',
  }[tone]
  return (
    <div className={`rounded-lg border px-4 py-3 text-[13px] ${toneClass}`} role="status">
      <p className="font-semibold">{title}</p>
      {body ? <p className="mt-0.5">{body}</p> : null}
    </div>
  )
}

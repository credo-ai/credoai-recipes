import type { DiffState } from '../lib/diff'

// Renders under a field's own (still-editable) control once it's amber-tinted
// for a pending diff — a read-only "Upstream: {value}" caption plus Accept/
// Reject actions. Never a second input: the control above stays the single
// source of the live value.

interface DiffAnnotationProps {
  diff: DiffState
  onAccept: () => void
  onReject: () => void
}

function formatUpstreamValue(value: string | string[]): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? '(empty)' : value.join(', ')
  }
  return value === '' ? '(empty)' : value
}

export function DiffAnnotation({ diff, onAccept, onReject }: DiffAnnotationProps) {
  return (
    <div className="mt-1.5">
      <p className="text-xs text-[#9a6a00]">Upstream: {formatUpstreamValue(diff.newValue)}</p>
      <div className="mt-1.5 flex gap-2">
        <button
          type="button"
          className="rounded-md border border-[#bfe9d8] bg-[#eafaf3] px-3 py-1 text-xs font-semibold text-[#1f7a5a] hover:bg-[#dcf3ea]"
          onClick={onAccept}
        >
          Accept
        </button>
        <button
          type="button"
          className="rounded-md border border-gray-300 bg-white px-3 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
          onClick={onReject}
        >
          Reject (keep current)
        </button>
      </div>
    </div>
  )
}

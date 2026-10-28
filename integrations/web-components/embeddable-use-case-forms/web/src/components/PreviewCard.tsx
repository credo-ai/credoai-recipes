import type { ReactNode } from 'react'

import { sanitizeFooterHtml } from '../lib/sanitize-html'
import type { MetadataRow } from '../types'

// The read-only preview card's building blocks, shared by the transparency
// card's Preview tab (CardPreview) and <credo-ai-use-case-preview>, so the
// two render identical markup.

// 2px solid black top border marking the start of a boundary-ruled block
// (a questionnaire section, the metadata block, Models, Vendors).
const SECTION_RULE = 'mt-[3.5px] border-t-2 border-black pt-3'
const SECTION_LABEL = 'text-[12px] font-bold uppercase tracking-wide text-black'

function PreviewValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <span className="flex flex-wrap justify-end gap-1">
        {value
          .filter((v): v is string => typeof v === 'string')
          .map((v) => (
            <span
              key={v}
              className="rounded-[6px] px-2 py-0.5 text-[12px] font-semibold"
              style={{ backgroundColor: '#f3f3f8', color: '#41006b' }}
            >
              {v}
            </span>
          ))}
      </span>
    )
  }
  return <span className="text-[13px] font-bold text-gray-900">{String(value)}</span>
}

export function PreviewRow({ label, value }: { label: string; value: unknown }) {
  return (
    <div
      className="flex items-start justify-between gap-4 border-b py-2 text-[13px]"
      style={{ borderColor: '#f0f0f4' }}
    >
      <span className="min-w-0" style={{ color: '#4a4a55' }}>
        {label}
      </span>
      {/* The value keeps its width (a short answer never breaks mid-word) up
          to 60% of the row; past that, a long unbroken value (a URL, raw
          JSON) wraps inside the card instead of overflowing it. */}
      <div className="max-w-[60%] shrink-0 text-right [overflow-wrap:anywhere]">
        <PreviewValue value={value} />
      </div>
    </div>
  )
}

// A boundary-ruled block, labelled when `title` is given.
export function PreviewBlock({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className={SECTION_RULE}>
      {title ? <div className={SECTION_LABEL}>{title}</div> : null}
      {children}
    </div>
  )
}

export function MetadataRowsBlock({ rows }: { rows: MetadataRow[] }) {
  if (rows.length === 0) {
    return null
  }
  return (
    <PreviewBlock>
      {rows.map((row) => (
        <div
          key={row.key}
          className="flex items-start justify-between gap-4 border-b py-2 text-[13px]"
          style={{ borderColor: '#f0f0f4' }}
        >
          <span style={{ color: '#4a4a55' }}>{row.label}</span>
          <span className="text-right text-[13px] font-bold text-gray-900">
            {row.value ?? '—'}
          </span>
        </div>
      ))}
    </PreviewBlock>
  )
}

export function EntityListBlock({ title, names }: { title: string; names: string[] }) {
  const filtered = names.filter((name) => Boolean(name?.trim()))
  if (filtered.length === 0) {
    return null
  }
  return (
    <PreviewBlock title={title}>
      {filtered.map((name) => (
        <div key={name} className="py-1 text-[13px] text-gray-900">
          {name}
        </div>
      ))}
    </PreviewBlock>
  )
}

// The card itself: optional hero, the ruled blocks, and the sanitized footer.
export function PreviewCardFrame({
  header,
  footerHtml,
  children,
}: {
  header: ReactNode
  footerHtml: string
  children: ReactNode
}) {
  return (
    <div className="px-5 pb-8 pt-6">
      <div className="mx-auto max-w-[460px] overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        {header}
        <div className="px-[22px] pb-2">{children}</div>
        <div
          className="border-t px-[22px] py-3 text-center text-[12px]"
          style={{ backgroundColor: '#fbf8ff', color: '#757582', borderColor: '#f0f0f4' }}
          dangerouslySetInnerHTML={{ __html: sanitizeFooterHtml(footerHtml) }}
        />
      </div>
    </div>
  )
}

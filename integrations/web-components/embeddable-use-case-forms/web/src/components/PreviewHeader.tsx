import type { UseCaseInfo } from '../types'

interface PreviewHeaderProps {
  useCase: UseCaseInfo
  // Set only when statusQuestionId/OPTIONS are configured and the
  // current answer matches one of the configured values.
  statusBadge: string | null
  // Mirrors the field-driven "Published" pill already shown in the
  // Edit/Setup topbar (CardToolbar) — distinct from statusBadge, which is
  // questionnaire-answer-driven.
  isCardPublished: boolean
  cardStatus: string | null
  // `hide-header-text`: drop the title/description; the host already shows them.
  hideHeaderText: boolean
}

function HeroBadges({
  showPublishedBadge,
  cardStatus,
  statusBadge,
}: {
  showPublishedBadge: boolean
  cardStatus: string | null
  statusBadge: string | null
}) {
  return (
    <>
      {showPublishedBadge && (
        <span
          title={cardStatus ?? undefined}
          data-testid="tc-preview-published-badge"
          className="max-w-[140px] flex-shrink-0 truncate rounded-full border border-[#16a34a] bg-[#dcfce7] px-3 py-1 text-[12px] font-semibold text-[#14532d]"
        >
          {cardStatus}
        </span>
      )}
      {statusBadge && (
        <span
          title={statusBadge}
          data-testid="tc-preview-status-badge"
          className="max-w-[160px] flex-shrink-0 truncate rounded-full border border-white/[.45] bg-white/[.16] px-[10px] py-[3px] text-[11px] font-semibold"
        >
          {statusBadge}
        </span>
      )}
    </>
  )
}

// The hero band. Omitted entirely (by the caller) unless at least one of
// name/description/statusBadge/the Published badge is present — or the header
// text is hidden, in which case the band always stays (blank when there are
// no badges) so the card doesn't open straight onto a section rule.
export function PreviewHeader({
  useCase,
  statusBadge,
  isCardPublished,
  cardStatus,
  hideHeaderText,
}: PreviewHeaderProps) {
  const name = hideHeaderText ? null : useCase.name
  const description = hideHeaderText ? null : useCase.description
  const showPublishedBadge = isCardPublished && Boolean(cardStatus)
  const hasBadges = showPublishedBadge || Boolean(statusBadge)
  const badges = (
    <HeroBadges showPublishedBadge={showPublishedBadge} cardStatus={cardStatus} statusBadge={statusBadge} />
  )

  // No header text: a compact cap with the badges inline and leading, rather
  // than stacked against empty space.
  if (!name && !description) {
    return (
      <div
        data-testid="tc-preview-hero"
        className="flex min-h-6 flex-wrap items-center gap-2 px-[22px] py-3 text-white"
        style={{ backgroundColor: '#7c03ca' }}
      >
        {badges}
      </div>
    )
  }

  return (
    <div
      data-testid="tc-preview-hero"
      className="px-[22px] py-[18px] text-white"
      style={{ backgroundColor: '#7c03ca' }}
    >
      {name && (
        <div data-testid="tc-preview-title" className="text-[21px] font-bold leading-[1.2]">
          {name}
        </div>
      )}
      {(description || hasBadges) && (
        <div className={`flex items-end justify-end gap-2 ${name ? 'mt-2.5' : ''}`}>
          {description && (
            <p
              data-testid="tc-preview-description"
              className="min-w-0 flex-1 text-[13px]"
              style={{ opacity: 0.92 }}
            >
              {description}
            </p>
          )}
          {hasBadges && <div className="flex flex-shrink-0 flex-col items-end gap-1.5">{badges}</div>}
        </div>
      )}
    </div>
  )
}

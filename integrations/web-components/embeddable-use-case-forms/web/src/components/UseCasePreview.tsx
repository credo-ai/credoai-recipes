import { useCallback, useEffect, useMemo, useState } from 'react'

import { GENERIC_ERROR_MESSAGE, errorMessage, userFacingErrorMessage } from '../api/errors'
import type { CredoClient } from '../api/credo'
import { normalizeAnswers, parseQuestionnaire, parseUseCase } from '../lib/compose'
import { parseCustomFieldDefs } from '../lib/custom-fields'
import { parseJsonStringArray } from '../lib/json-config'
import { buildMetadataRows } from '../lib/preview-metadata'
import {
  type PreviewRowData,
  type PreviewSectionData,
  buildCustomFieldRows,
  buildPreviewSections,
  parseCustomFieldNames,
  selectQuestionnaires,
  unmatchedQuestionnaireIds,
} from '../lib/use-case-preview'
import type { CredoStatus, MetadataRow, Questionnaire, UseCaseInfo } from '../types'
import { ElementLoading } from './ElementShell'
import {
  EntityListBlock,
  MetadataRowsBlock,
  PreviewBlock,
  PreviewCardFrame,
  PreviewRow,
} from './PreviewCard'
import { PreviewHeader } from './PreviewHeader'

export type PreviewEvent = 'loaded' | 'error'

interface UseCasePreviewProps {
  config: CredoStatus
  credo: CredoClient
  useCaseId: string
  // Raw `questionnaire-order` attribute: a JSON array of questionnaire ids.
  questionnaireOrder: string
  emit: (event: PreviewEvent, detail: unknown) => void
}

interface LoadedPreview {
  useCase: UseCaseInfo
  attached: Questionnaire[]
  sections: PreviewSectionData[]
  customFieldRows: PreviewRowData[]
  metadataRows: MetadataRow[]
}

const LOG_PREFIX = '[credo-ai-use-case-preview]'

// The transparency card's Preview, standalone and read-only: same markup
// (components/PreviewCard.tsx), but no saved layout and no section or question
// controls. Questionnaire order and visibility come from attributes; see
// lib/use-case-preview.ts for the rules.
export function UseCasePreview({ config, credo, useCaseId, questionnaireOrder, emit }: UseCasePreviewProps) {
  const [preview, setPreview] = useState<LoadedPreview | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  const allowedIds = useMemo(() => parseJsonStringArray(config.allowed_questionnaires), [config])
  const orderIds = useMemo(() => parseJsonStringArray(questionnaireOrder), [questionnaireOrder])
  const customFieldNames = useMemo(() => parseCustomFieldNames(config.custom_field_names), [config])

  useEffect(() => {
    if (customFieldNames.malformed) {
      console.warn(`${LOG_PREFIX} custom-fields is not a JSON array of field names; no custom fields will show`)
    }
  }, [customFieldNames])

  useEffect(() => {
    let cancelled = false
    setIsLoading(true)
    setLoadError(null)

    void (async () => {
      try {
        const [useCaseDoc, evidences, fieldValues, rawDefs] = await Promise.all([
          credo.getUseCase(useCaseId),
          credo.getUseCaseEvidences(useCaseId),
          credo.getUseCaseCustomFields(useCaseId),
          credo.listCustomFieldDefs('use_case'),
        ])
        const { useCase, questionnaireIds } = parseUseCase(useCaseDoc)
        const attached: Questionnaire[] = []
        for (const id of questionnaireIds) {
          attached.push(parseQuestionnaire(await credo.getQuestionnaire(id)))
        }
        if (cancelled) {
          return
        }
        const shown = selectQuestionnaires(attached, allowedIds, orderIds)
        setPreview({
          useCase,
          attached,
          sections: buildPreviewSections(shown, normalizeAnswers(evidences, shown)),
          customFieldRows: buildCustomFieldRows(
            parseCustomFieldDefs(rawDefs),
            fieldValues,
            customFieldNames.names,
          ),
          metadataRows: buildMetadataRows(useCase.attributes, parseJsonStringArray(config.use_case_fields)),
        })
        emit('loaded', { useCaseId, questionnaireIds: shown.map((q) => q.id) })
      } catch (err) {
        if (!cancelled) {
          console.warn(`${LOG_PREFIX} load failed:`, errorMessage(err))
          setLoadError(userFacingErrorMessage(err))
          emit('error', { useCaseId, message: errorMessage(err) })
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [credo, config, useCaseId, allowedIds, orderIds, customFieldNames, emit, attempt])

  const unmatchedKey = useMemo(() => {
    if (!preview) {
      return ''
    }
    return [
      ...unmatchedQuestionnaireIds(preview.attached, allowedIds).map((id) => `allowed-questionnaires: ${id}`),
      ...unmatchedQuestionnaireIds(preview.attached, orderIds).map((id) => `questionnaire-order: ${id}`),
    ].join(', ')
  }, [preview, allowedIds, orderIds])
  useEffect(() => {
    if (unmatchedKey !== '') {
      console.warn(`${LOG_PREFIX} questionnaire ids not attached to this use case: ${unmatchedKey}`)
    }
  }, [unmatchedKey])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  if (isLoading) {
    return <ElementLoading label="Loading use case…" />
  }

  if (loadError || !preview) {
    return (
      <div
        role="alert"
        className="mx-auto w-full max-w-[460px] rounded-xl border border-red-200 bg-white px-5 py-8 shadow-sm"
      >
        <p className="text-sm font-semibold text-red-900">Unable to load use case preview</p>
        <p className="mb-4 mt-1 text-sm text-red-700">{loadError ?? GENERIC_ERROR_MESSAGE}</p>
        <button
          type="button"
          className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-[13px] font-semibold text-gray-700 hover:bg-gray-50"
          onClick={retry}
        >
          Retry
        </button>
      </div>
    )
  }

  const { useCase } = preview
  const hideHeaderText = config.hide_header_text
  // The card's hero rule, minus its two badges (the card-status field and the
  // status question are transparency-card concepts).
  const hasHero = hideHeaderText || Boolean(useCase.name) || Boolean(useCase.description)

  return (
    <PreviewCardFrame
      footerHtml={config.preview_footer_text}
      header={
        hasHero ? (
          <PreviewHeader
            useCase={useCase}
            statusBadge={null}
            isCardPublished={false}
            cardStatus={null}
            hideHeaderText={hideHeaderText}
          />
        ) : null
      }
    >
      {preview.customFieldRows.length > 0 ? (
        <PreviewBlock title="Additional information">
          {preview.customFieldRows.map((row) => (
            <PreviewRow key={row.id} label={row.label} value={row.value} />
          ))}
        </PreviewBlock>
      ) : null}
      {preview.sections.map((section) => (
        <PreviewBlock key={section.key} title={section.title}>
          {section.rows.map((row) => (
            <PreviewRow key={row.id} label={row.label} value={row.value} />
          ))}
        </PreviewBlock>
      ))}
      <MetadataRowsBlock rows={preview.metadataRows} />
      <EntityListBlock title="Models" names={useCase.connected_model_names} />
      <EntityListBlock title="Vendors" names={useCase.connected_vendor_names} />
    </PreviewCardFrame>
  )
}

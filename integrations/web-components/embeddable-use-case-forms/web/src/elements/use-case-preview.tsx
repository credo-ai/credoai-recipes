import { useMemo } from 'react'

import { CredoClient } from '../api/credo'
import { MissingTokenSourceError, resolveTokenSource } from '../api/tokens'
import { ElementNotice, ElementRoot } from '../components/ElementShell'
import { type PreviewEvent, UseCasePreview } from '../components/UseCasePreview'
import { type UseCasePreviewProps, resolvePreviewConfig } from '../config'
import { attributeKey } from '../wc/attribute-key'
import { defineElement } from '../wc/define-element'
import { useHostEvents } from '../wc/events'

// <credo-ai-use-case-preview> — a read-only preview of a use case, rendered
// exactly like the transparency card's Preview tab. Questionnaire order and
// visibility, and
// which custom fields show, are attributes — see ../config.ts.

function Element(props: UseCasePreviewProps) {
  const { elementRef, emit } = useHostEvents<PreviewEvent>()

  // Memoized on the attributes, not on props identity — see attributeKey.
  const propsKey = attributeKey(props)
  const { config, credo, credentialError } = useMemo(() => {
    const resolved = resolvePreviewConfig(props)
    try {
      return {
        config: resolved,
        credo: new CredoClient(resolved, resolveTokenSource(props)),
        credentialError: null,
      }
    } catch (err) {
      return {
        config: resolved,
        credo: null,
        credentialError: err instanceof MissingTokenSourceError ? err.message : String(err),
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propsKey])

  if (credentialError) {
    return (
      <ElementRoot elementRef={elementRef}>
        <ElementNotice title="Missing credentials" body={credentialError} />
      </ElementRoot>
    )
  }

  if (!credo || !config.configured || !config.use_case_id) {
    return (
      <ElementRoot elementRef={elementRef}>
        <ElementNotice
          title="Not configured"
          body="Set the tenant and use-case-id attributes on <credo-ai-use-case-preview>."
        />
      </ElementRoot>
    )
  }

  return (
    <ElementRoot elementRef={elementRef}>
      <UseCasePreview
        config={config}
        credo={credo}
        useCaseId={config.use_case_id}
        questionnaireOrder={props.questionnaireOrder ?? ''}
        emit={emit}
      />
    </ElementRoot>
  )
}

defineElement<UseCasePreviewProps>('credo-ai-use-case-preview', Element, {
  apiUrl: 'string',
  integrationServiceApiUrl: 'string',
  tenant: 'string',
  useCaseId: 'string',
  questionnaireOrder: 'string',
  allowedQuestionnaires: 'string',
  customFields: 'string',
  useCaseFields: 'string',
  previewFooterText: 'string',
  hideHeaderText: 'string',
  integrationToken: 'string',
  tokenEndpoint: 'string',
})

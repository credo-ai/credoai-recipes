import { ElementNotice, ElementRoot } from '../components/ElementShell'
import {
  type SubmitFormEvent,
  UseCaseSubmitForm,
} from '../components/use-cases/UseCaseSubmitForm'
import type { UseCaseSubmitFormProps } from '../config'
import { defineElement } from '../wc/define-element'
import { useHostEvents } from '../wc/events'
import { useFormElementSetup } from './form-element'

// credo-ai-use-case-intake-form — registers a use case against a fixed questionnaire,
// rendering every question up front.
// Attribute contract matches Credo AI's hosted web component, plus
// `token-endpoint` / `integration-token` (see ../config.ts).

function Element(props: UseCaseSubmitFormProps) {
  const { elementRef, emit } = useHostEvents<SubmitFormEvent>()
  const { config, credo, platform, error } = useFormElementSetup(props)

  if (error || !credo || !platform) {
    return (
      <ElementRoot elementRef={elementRef}>
        <ElementNotice title="Not configured" body={error ?? 'Missing credentials.'} />
      </ElementRoot>
    )
  }

  return (
    <ElementRoot elementRef={elementRef}>
      <UseCaseSubmitForm
        config={config}
        credo={credo}
        platform={platform}
        questionnaireId={props.questionnaireId ?? ''}
        appUrl={props.appUrl ?? ''}
        source={props.source ?? ''}
        allowSubmit={props.allowSubmit ?? true}
        hideCustomFields={props.hideCustomFields ?? false}
        disableQuestionnaireSelection={props.disableQuestionnaireSelection ?? false}
        conditional={false}
        emit={emit}
      />
    </ElementRoot>
  )
}

defineElement<UseCaseSubmitFormProps>('credo-ai-use-case-intake-form', Element, {
  apiUrl: 'string',
  appUrl: 'string',
  integrationServiceApiUrl: 'string',
  tenant: 'string',
  user: 'string',
  questionnaireId: 'string',
  source: 'string',
  allowSubmit: 'boolean',
  hideCustomFields: 'boolean',
  disableQuestionnaireSelection: 'boolean',
  integrationToken: 'string',
  tokenEndpoint: 'string',
})

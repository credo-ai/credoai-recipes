import { ElementNotice, ElementRoot } from '../components/ElementShell'
import { type EditFormEvent, UseCaseEditForm } from '../components/use-cases/UseCaseEditForm'
import type { UseCaseEditFormProps } from '../config'
import { defineElement } from '../wc/define-element'
import { useHostEvents } from '../wc/events'
import { useFormElementSetup } from './form-element'

// <credo-ai-use-case-edit-form> — edits an existing use case: its name,
// description, custom fields, and every questionnaire attached to it, with the
// same conditional-question evaluation as the create form.
// Attribute contract matches Credo AI's hosted web component, plus
// `token-endpoint` / `integration-token` (see ../config.ts).

function Element(props: UseCaseEditFormProps) {
  const { elementRef, emit } = useHostEvents<EditFormEvent>()
  const { config, credo, platform, error } = useFormElementSetup(props)

  if (error || !credo || !platform) {
    return (
      <ElementRoot elementRef={elementRef}>
        <ElementNotice title="Not configured" body={error ?? 'Missing credentials.'} />
      </ElementRoot>
    )
  }

  if (!props.useCaseId) {
    return (
      <ElementRoot elementRef={elementRef}>
        <ElementNotice
          title="Not configured"
          body="Set the use-case-id attribute on <credo-ai-use-case-edit-form>."
        />
      </ElementRoot>
    )
  }

  return (
    <ElementRoot elementRef={elementRef}>
      <UseCaseEditForm
        config={config}
        credo={credo}
        platform={platform}
        useCaseId={props.useCaseId}
        appUrl={props.appUrl ?? ''}
        allowSubmit={props.allowSubmit ?? true}
        hideCustomFields={props.hideCustomFields ?? false}
        emit={emit}
      />
    </ElementRoot>
  )
}

defineElement<UseCaseEditFormProps>('credo-ai-use-case-edit-form', Element, {
  apiUrl: 'string',
  appUrl: 'string',
  integrationServiceApiUrl: 'string',
  tenant: 'string',
  user: 'string',
  useCaseId: 'string',
  allowSubmit: 'boolean',
  hideCustomFields: 'boolean',
  integrationToken: 'string',
  tokenEndpoint: 'string',
})

import type { CredoStatus } from './types'

// Element-owned configuration. Every value below arrives as an attribute on
// the custom element — nothing is read from `window`, and nothing comes from
// the backend, which only mints tokens.
//
// The attribute names deliberately match Credo AI's hosted web components,
// so an existing embed keeps working unchanged. Two attributes are additions,
// and are marked as such below.

export interface TransparencyCardProps {
  // Shared with Credo AI's hosted web components
  apiUrl?: string
  integrationServiceApiUrl?: string
  tenant?: string
  useCaseId?: string
  user?: string
  mode?: string
  previewFooterText?: string
  statusQuestionId?: string
  statusQuestionOptions?: string
  customFields?: string
  cardStatusFieldName?: string
  cardStatusPublishedValue?: string
  useCaseFields?: string
  allowedQuestionnaires?: string
  blockingQuestionnaires?: string
  defaultOrderAndVisibility?: string
  hideHeaderText?: string
  // Additions, both forced by this recipe's "the API key never reaches the
  // browser" rule:
  // - `integrationToken`: the short-lived token minted from the API key
  //   (see backend/app/credo_auth.py); it authenticates every Credo AI API
  //   call the elements make, in place of `jwt-token`.
  // - `tokenEndpoint`: a host endpoint that mints it, letting the element
  //   refresh it on a 401 instead of dying when the first token expires.
  integrationToken?: string
  tokenEndpoint?: string
  layoutFieldName?: string
}

function str(value: string | undefined, fallback = ''): string {
  return value || fallback
}

function strOrNull(value: string | undefined): string | null {
  return value || null
}

// The `mode` attribute's tiers and this recipe's permission tiers are the same three
// levels under different names; `permissions.ts` speaks the latter.
const MODE_TO_PERMISSION_LEVEL: Record<string, string> = {
  setup: 'full',
  toggle: 'full',
  edit: 'restricted',
  preview: 'readonly',
}

export function permissionLevelFromMode(mode: string | undefined): string {
  if (!mode) {
    return ''
  }
  // An unrecognized value falls through to permissions.ts, which fails it
  // closed to the middle tier rather than guessing generously.
  return MODE_TO_PERMISSION_LEVEL[mode.trim().toLowerCase()] ?? mode
}

const TRUTHY_ATTRIBUTE_VALUES = new Set(['', 'true', '1', 'yes'])
const FALSY_ATTRIBUTE_VALUES = new Set(['false', '0', 'no'])

// `hide-header-text` is a string attribute, not an r2wc boolean, so that the
// accepted spellings match Credo AI's hosted web components exactly. Unrecognized values fail closed
// to "shown" (the default) with a warning.
export function parseHideHeaderText(raw: string | undefined): boolean {
  if (raw === undefined) {
    return false
  }
  const normalized = raw.trim().toLowerCase()
  if (TRUTHY_ATTRIBUTE_VALUES.has(normalized)) {
    return true
  }
  if (!FALSY_ATTRIBUTE_VALUES.has(normalized)) {
    console.warn(
      `[credo-ai] hideHeaderText "${raw}" is not a boolean value — the title and description will still show.`,
    )
  }
  return false
}

export function resolveCardConfig(props: TransparencyCardProps): CredoStatus {
  return {
    configured: Boolean(props.tenant && props.useCaseId && props.user),
    tenant: strOrNull(props.tenant),
    private_api_base_url: str(props.apiUrl, 'https://api.credo.ai'),
    integration_api_base_url: str(
      props.integrationServiceApiUrl || props.apiUrl,
      'https://api.credo.ai',
    ),
    use_case_id: strOrNull(props.useCaseId),
    user_email: strOrNull(props.user),
    layout_field_name: str(
      props.layoutFieldName,
      'Transparency Card Section Visibility Order',
    ),
    status_field_name: str(props.cardStatusFieldName, 'Transparency Card Status'),
    published_status_value: str(props.cardStatusPublishedValue, 'Published'),
    permission_level: permissionLevelFromMode(props.mode),
    allowed_questionnaires: str(props.allowedQuestionnaires),
    blocking_questionnaires: str(props.blockingQuestionnaires),
    status_question_id: strOrNull(props.statusQuestionId),
    status_question_options: str(props.statusQuestionOptions),
    custom_field_names: str(props.customFields),
    use_case_fields: str(props.useCaseFields),
    default_layout: strOrNull(props.defaultOrderAndVisibility),
    preview_footer_text: str(props.previewFooterText, 'Powered by Credo AI'),
    hide_header_text: parseHideHeaderText(props.hideHeaderText),
  }
}

// ---- Use-case form elements ------------------------------------------------

// Shared by all three use-case forms. Same attribute names as Credo AI's
// hosted web components, with the same two auth additions as above.
export interface UseCaseFormBaseProps {
  apiUrl?: string
  appUrl?: string
  integrationServiceApiUrl?: string
  tenant?: string
  user?: string
  allowSubmit?: boolean
  hideCustomFields?: boolean
  integrationToken?: string
  tokenEndpoint?: string
}

export interface UseCaseSubmitFormProps extends UseCaseFormBaseProps {
  questionnaireId?: string
  source?: string
  disableQuestionnaireSelection?: boolean
}

export interface UseCaseEditFormProps extends UseCaseFormBaseProps {
  useCaseId?: string
}

// The forms need far less than the card: the API surfaces, the tenant, and
// who to attribute the write to. The card-specific fields keep their defaults
// and are simply unused.
export function resolveFormConfig(props: UseCaseFormBaseProps): CredoStatus {
  return resolveCardConfig({
    apiUrl: props.apiUrl,
    integrationServiceApiUrl: props.integrationServiceApiUrl,
    tenant: props.tenant,
    user: props.user,
  })
}

// ---- Use-case preview element -----------------------------------------------

// Read-only, so no `user` and no `mode`. The shared names (`custom-fields`,
// `allowed-questionnaires`, `use-case-fields`, `preview-footer-text`,
// `hide-header-text`) keep the card's formats; `questionnaire-order` is new.
// `custom-fields` differs in one way: unset means every field, not none.
export interface UseCasePreviewProps {
  apiUrl?: string
  integrationServiceApiUrl?: string
  tenant?: string
  useCaseId?: string
  questionnaireOrder?: string
  allowedQuestionnaires?: string
  customFields?: string
  useCaseFields?: string
  previewFooterText?: string
  hideHeaderText?: string
  integrationToken?: string
  tokenEndpoint?: string
}

export function resolvePreviewConfig(props: UseCasePreviewProps): CredoStatus {
  const config = resolveCardConfig({
    apiUrl: props.apiUrl,
    integrationServiceApiUrl: props.integrationServiceApiUrl,
    tenant: props.tenant,
    useCaseId: props.useCaseId,
    allowedQuestionnaires: props.allowedQuestionnaires,
    customFields: props.customFields,
    useCaseFields: props.useCaseFields,
    previewFooterText: props.previewFooterText,
    hideHeaderText: props.hideHeaderText,
  })
  return { ...config, configured: Boolean(props.tenant && props.useCaseId) }
}

// ---- Use-case preview grid element ------------------------------------------

// The preview element's attributes, applied to every card, minus
// `use-case-id` (the grid lists the tenant's use cases itself) plus
// `page-size`: cards per page, 1–100, default 6.
export interface UseCasePreviewGridProps extends Omit<UseCasePreviewProps, 'useCaseId'> {
  pageSize?: string
}

export const DEFAULT_GRID_PAGE_SIZE = 6

export function parseGridPageSize(raw: string | undefined): number {
  const size = Number(raw)
  return Number.isInteger(size) && size >= 1 && size <= 100 ? size : DEFAULT_GRID_PAGE_SIZE
}

export function resolvePreviewGridConfig(props: UseCasePreviewGridProps): CredoStatus {
  const config = resolvePreviewConfig({ ...props, useCaseId: undefined })
  return { ...config, configured: Boolean(props.tenant) }
}

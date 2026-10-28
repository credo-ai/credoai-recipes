export type AnswerValue = string | string[] | null

export interface Question {
  id: string
  text: string
  description?: string | null
  evidence_type: string // text|short_text|select|file|date|number|user
  select_options: string[]
  multiple: boolean
  required: boolean
  // Conditional question: only shown once a trigger reveals it (the
  // questionnaire preview marks it visible). Only set by the use-case forms'
  // parse — the card drops these questions entirely.
  hidden?: boolean
}

export interface Section {
  id: string
  title: string
  description?: string | null
  questions: Question[]
}

export interface QuestionnaireSummary {
  id: string
  // Base key shared by every version (e.g. "TCV2" for id "TCV2+25").
  key?: string | null
  name: string
  version?: number | null
}

export interface Questionnaire extends QuestionnaireSummary {
  sections: Section[]
}

// A question as it appears in the merged, cross-questionnaire section list —
// tagged with its origin questionnaire so save/hide logic can route back to
// the right (questionnaire_id, section_id, question_id) triple even when a
// section id is shared by more than one questionnaire.
export interface MergedQuestion extends Question {
  questionnaire_id: string
}

export interface MergedSection {
  id: string
  title: string
  description?: string | null
  questions: MergedQuestion[]
}

export interface LayoutEntry {
  section_id: string
  is_visible: boolean
  // Individually-hidden question ids within this section. Hide-only — there
  // is no per-question reordering, only per-section.
  hidden_question_ids?: string[]
}

// Whole-questionnaire visibility override. A pure display-time override on
// top of each section's own LayoutEntry — turning a questionnaire back on
// reverts every one of its questions to exactly whatever hidden state its
// own section/question toggles already held; this array never mutates them.
export interface QuestionnaireVisibilityEntry {
  questionnaire_id: string
  is_visible: boolean
}

export interface UseCaseInfo {
  id: string
  name: string
  description?: string | null
  // Full raw attribute bag (snake_case API wire names, e.g. "risk_classification_level")
  // so Preview metadata rows can look up arbitrary configured property names
  // without this file needing to know them in advance.
  attributes: Record<string, unknown>
  // Connected models'/vendors' display names, tolerant of missing relationship data.
  connected_model_names: string[]
  connected_vendor_names: string[]
}

export interface CustomFieldDef {
  id: string
  name: string
  element_type: string
  options: string[]
  multiple: boolean
}

export interface CustomFieldEntry {
  def: CustomFieldDef
  value: unknown
}

export interface MetadataRow {
  key: string
  label: string
  value: string | null
}

export type PermissionLevel = 'full' | 'restricted' | 'readonly'

export interface CardResponse {
  use_case: UseCaseInfo
  // Every questionnaire attached to the use case, unfiltered by the allow
  // list — the header/status must stay accurate even when the allow list
  // excludes everything.
  questionnaires: QuestionnaireSummary[]
  // Attached AND allow-listed (or all, if no allow list configured) — full
  // sections, used for save/refresh routing.
  included_questionnaires: Questionnaire[]
  // Merged, deduplicated section list built from included_questionnaires —
  // what the Edit/Layout tabs actually render.
  sections: MergedSection[]
  excluded_questionnaire_names: string[]
  // Set when the allow list excludes every attached questionnaire; the tab
  // body renders this message instead of any section content.
  mismatch_error: string | null
  answers: Record<string, AnswerValue>
  // Tenant custom fields configured via customFieldNames, resolved
  // against the tenant's field catalog. Empty if none match.
  custom_fields: CustomFieldEntry[]
  // Preview metadata Label/Value rows, from useCaseFields.
  preview_metadata_rows: MetadataRow[]
  card_mode: 'setup' | 'edit'
  card_status: string | null
  is_published: boolean
  layout: LayoutEntry[]
  layout_persistence_available: boolean
  // Reconciled against included_questionnaires — always one entry per
  // included questionnaire, defaulting to visible.
  questionnaire_visibility: QuestionnaireVisibilityEntry[]
}

export interface AnswersResponse {
  answers: Record<string, AnswerValue>
  card_mode: 'setup' | 'edit'
  card_status: string | null
  is_published: boolean
  // Set when the allow list excludes every attached questionnaire — mirrors
  // CardResponse.mismatch_error. Refresh-from-upstream skips diff computation
  // entirely when this is set (nothing valid to compare against).
  mismatch_error: string | null
}

export interface SaveCardRequest {
  // Current live form values, keyed by question id.
  answers: Record<string, AnswerValue>
  // Baseline as of the last load/save — used to detect which questions were
  // cleared back to empty, so their upstream evidence can be deleted.
  loaded_answers: Record<string, AnswerValue>
  layout?: LayoutEntry[] | null
  questionnaire_visibility?: QuestionnaireVisibilityEntry[] | null
}

export interface SaveResult {
  id: string
  layout_persisted: boolean
}

export interface CredoStatus {
  configured: boolean
  tenant: string | null
  private_api_base_url: string
  integration_api_base_url: string
  use_case_id: string | null
  user_email: string | null
  layout_field_name: string
  status_field_name: string
  published_status_value: string
  permission_level: string
  allowed_questionnaires: string
  // Raw JSON array of base questionnaire ids — see lib/blocking-questionnaires.ts.
  blocking_questionnaires: string
  status_question_id: string | null
  status_question_options: string
  custom_field_names: string
  use_case_fields: string
  default_layout: string | null
  preview_footer_text: string
  // Preview drops the use case title/description (host already shows them).
  hide_header_text: boolean
}

// Short-lived token minted by the token service from its API key. One token
// serves both the integration service and the platform API.
export interface CredoTokens {
  integration_api_token: string
}

// Form values are keyed by question id, plus the reserved __layout key so
// layout changes participate in react-hook-form's dirty tracking.
export type CardFormValues = Record<string, unknown>
export const LAYOUT_FORM_KEY = '__layout'
export const QUESTIONNAIRE_VISIBILITY_FORM_KEY = '__questionnaireVisibility'

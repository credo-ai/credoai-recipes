import { customFieldFormKey, resolveConfiguredCustomFields } from './custom-fields'
import { buildMetadataRows } from './preview-metadata'
import { buildQuestionnaireVisibility } from './questionnaire-visibility'
import type {
  AnswerValue,
  CardResponse,
  CustomFieldDef,
  LayoutEntry,
  MergedSection,
  Question,
  Questionnaire,
  QuestionnaireSummary,
  QuestionnaireVisibilityEntry,
  Section,
  UseCaseInfo,
} from '../types'

// Pure functions that compose raw Credo AI responses into the card model.
// No I/O here — everything takes already-fetched JSON:API documents so it
// can be unit-tested against fixtures.

// Answers for these evidence types can't be edited by this recipe's form
// controls, so they are rendered read-only and excluded from the save payload.
export const UNSUPPORTED_EVIDENCE_TYPES = new Set(['file', 'user'])

export interface SaveQuestionEntry {
  questionnaire_id: string
  section_id: string
  question_id: string
  value: string | string[]
}

// Integration-service PATCH body (already snake_case on the wire). A custom
// field's value type is left as `unknown` rather than `string` — the wire
// shape for e.g. a checkbox-type field's boolean value is unconfirmed against
// a real tenant; forcing a string coercion here could silently be wrong.
export interface SavePayload {
  questions: SaveQuestionEntry[]
  user: string
  custom_fields?: { custom_field_id: string; value: unknown }[]
}

type Dict = Record<string, unknown>

function rec(v: unknown): Dict {
  return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Dict) : {}
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

function strOr(v: unknown, fallback = ''): string {
  return typeof v === 'string' && v !== '' ? v : fallback
}

// Parse a `GET /questionnaires/{id}` JSON:API document.
// Hidden questions are dropped — they are not part of the card. The use-case
// forms pass `keepHidden`: there a hidden question is a conditional one the
// preview can reveal, which the Credo AI app's forms render.
export function parseQuestionnaire(
  doc: unknown,
  { keepHidden = false }: { keepHidden?: boolean } = {},
): Questionnaire {
  const data = rec(rec(doc).data)
  const attrs = rec(data.attributes)
  const sections: Section[] = []
  for (const rawSection of arr(attrs.sections).map(rec)) {
    const questions: Question[] = []
    for (const rawQ of arr(rawSection.questions).map(rec)) {
      if (rawQ.hidden && !keepHidden) {
        continue
      }
      questions.push({
        id: String(rawQ.id),
        text: strOr(rawQ.question),
        description: typeof rawQ.description === 'string' ? rawQ.description : null,
        evidence_type: strOr(rawQ.evidence_type, 'short_text'),
        select_options: arr(rawQ.select_options).map(String),
        multiple: Boolean(rawQ.multiple),
        required: Boolean(rawQ.required),
        ...(rawQ.hidden ? { hidden: true } : {}),
      })
    }
    sections.push({
      id: String(rawSection.id),
      title: strOr(rawSection.title),
      description:
        typeof rawSection.description === 'string' ? rawSection.description : null,
      questions,
    })
  }
  return {
    id: String(data.id),
    name: strOr(attrs.name),
    key: typeof attrs.key === 'string' ? attrs.key : null,
    version: typeof attrs.version === 'number' ? attrs.version : null,
    sections,
  }
}

export function summarizeQuestionnaire(q: Questionnaire): QuestionnaireSummary {
  return { id: q.id, name: q.name, version: q.version }
}

// Cross-reference a JSON:API `included` array against the use case's
// `models`/`vendors` relationships (confirmed live: `GET .../use_cases/{id}
// ?include=models,vendors` returns both relationship refs and the full
// related resources under `included`). Tolerant of missing relationship
// data, blank names, or no `include` having been requested at all. Kept
// separate (not merged into one list) since Preview renders Models and
// Vendors as two independent sections.
function parseConnectedEntityNames(doc: unknown): { models: string[]; vendors: string[] } {
  const included = arr(rec(doc).included)
  const byRef = new Map<string, string>()
  for (const item of included) {
    const itemRec = rec(item)
    const name = rec(itemRec.attributes).name
    if (typeof name === 'string' && name.trim() !== '') {
      byRef.set(`${String(itemRec.type)}:${String(itemRec.id)}`, name)
    }
  }
  const relationships = rec(rec(rec(doc).data).relationships)
  const namesFor = (relKey: string): string[] =>
    arr(rec(relationships[relKey]).data)
      .map((ref) => byRef.get(`${String(rec(ref).type)}:${String(rec(ref).id)}`))
      .filter((name): name is string => Boolean(name))
  return { models: namesFor('models'), vendors: namesFor('vendors') }
}

// Extract display info and attached questionnaire ids from a use case.
export function parseUseCase(doc: unknown): {
  useCase: UseCaseInfo
  questionnaireIds: string[]
} {
  const data = rec(rec(doc).data)
  const attrs = rec(data.attributes)
  const { models, vendors } = parseConnectedEntityNames(doc)
  return {
    useCase: {
      id: String(data.id),
      name: strOr(attrs.name),
      description: typeof attrs.description === 'string' ? attrs.description : null,
      attributes: attrs,
      connected_model_names: models,
      connected_vendor_names: vendors,
    },
    questionnaireIds: arr(attrs.questionnaire_ids).map(String),
  }
}

// Map evidences (`label.key` = question id, `data.value` = answer) to
// whichever of the given questionnaires' questions they belong to.
//
// Later evidences win, matching the web component's map-overwrite behavior
// (the evidence POST endpoint does not dedupe, so duplicates can exist).
export function normalizeAnswers(
  evidences: unknown[],
  questionnaires: Questionnaire[],
): Record<string, AnswerValue> {
  const questionIds = new Set(
    questionnaires.flatMap((q) => q.sections.flatMap((section) => section.questions.map((qq) => qq.id))),
  )
  const answers: Record<string, AnswerValue> = {}
  for (const evidence of evidences) {
    const attrs = rec(rec(evidence).attributes)
    const questionId = rec(attrs.label).key
    if (typeof questionId !== 'string' || !questionIds.has(questionId)) {
      continue
    }
    const value = rec(attrs.data).value
    if (Array.isArray(value)) {
      answers[questionId] = value
        .filter((v) => v !== null && v !== undefined)
        .map(String)
    } else if (value === null || value === undefined) {
      answers[questionId] = null
    } else {
      answers[questionId] = String(value)
    }
  }
  return answers
}

// 'edit' once any answer exists across the included questionnaires.
export function deriveCardMode(
  answers: Record<string, AnswerValue>,
): 'setup' | 'edit' {
  const hasAnswer = Object.values(answers).some(
    (v) => !(v === null || v === '' || (Array.isArray(v) && v.length === 0)),
  )
  return hasAnswer ? 'edit' : 'setup'
}

// Find a custom-field definition id by display name.
//
// Resolution must come from definitions (not the use case's values) so the
// field can be written even when it has no value yet.
export function resolveFieldId(defs: unknown[], name: string): string | null {
  for (const item of defs) {
    if (rec(rec(item).attributes).name === name) {
      return String(rec(item).id)
    }
  }
  return null
}

// Read a use case's value for a custom field definition id.
//
// Tolerates both an inline `custom_field_id` attribute and a JSON:API
// `custom_field` relationship.
export function readCustomFieldValue(
  values: unknown[],
  fieldId: string | null,
): unknown {
  if (fieldId === null) {
    return null
  }
  for (const item of values) {
    const attrs = rec(rec(item).attributes)
    let itemFieldId = attrs.custom_field_id
    if (itemFieldId === null || itemFieldId === undefined) {
      const relationship = rec(rec(rec(item).relationships).custom_field)
      itemFieldId = rec(relationship.data).id
    }
    if (itemFieldId !== undefined && String(itemFieldId) === fieldId) {
      return attrs.value ?? null
    }
  }
  return null
}

// Entries follow the Credo AI app's layout parser: an entry whose
// id isn't a string or whose isVisible isn't a boolean is skipped, not
// defaulted.
function parseVisibilityEntries(items: unknown): LayoutEntry[] {
  const entries: LayoutEntry[] = []
  for (const item of arr(items)) {
    const entry = rec(item)
    if (typeof entry.section !== 'string' || typeof entry.isVisible !== 'boolean') {
      continue
    }
    // The Credo AI app's shape, what this recipe writes: a per-question
    // array under `questions`, each `{question, isVisible}`.
    let hiddenQuestionIds = arr(entry.questions)
      .map(rec)
      .filter((q) => typeof q.question === 'string' && q.isVisible === false)
      .map((q) => q.question as string)
    // The superseded flat list of hidden ids, read as a fallback so
    // layouts it saved before switching to the app's shape still load.
    if (hiddenQuestionIds.length === 0) {
      hiddenQuestionIds = arr(entry.hiddenQuestions).filter((id): id is string => typeof id === 'string')
    }
    entries.push({
      section_id: entry.section,
      is_visible: entry.isVisible,
      ...(hiddenQuestionIds.length > 0 ? { hidden_question_ids: hiddenQuestionIds } : {}),
    })
  }
  return entries
}

// Custom field values and the default-order-and-visibility attribute are both
// JSON strings; anything else (or unparseable, or not an object) is no value.
function parseLayoutDoc(raw: unknown): Dict | null {
  if (typeof raw !== 'string') {
    return null
  }
  try {
    const parsed: unknown = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? (parsed as Dict) : null
  } catch {
    return null
  }
}

// Parse the layout custom field's stored JSON into a flat entry list.
//
// Understands the Credo AI app's shape, which this recipe writes,
// `{"visibility": [{"section","isVisible","questions"?}], "questionnaires": [...]}`;
// the superseded `{"version": 3, "visibility": [{..., "hiddenQuestions"?}]}`
// (same `visibility` key, so it parses through the same path); and its older
// per-questionnaire-keyed shape `{"version": 2, "questionnaires": {qid: [...]}}}`
// (an object, unlike the app's `questionnaires` array), flattened across
// every sub-array in attached-questionnaire order and deduped by section id
// (first occurrence wins) so nothing saved by that model is lost on upgrade.
//
// As in the app, no valid entries means no saved layout (null), so an empty
// `visibility` falls back to the default rather than blocking it.
export function parseLayoutJson(raw: unknown): LayoutEntry[] | null {
  const doc = parseLayoutDoc(raw)
  if (doc === null) {
    return null
  }
  let entries: LayoutEntry[] = []
  if (Array.isArray(doc.visibility)) {
    entries = parseVisibilityEntries(doc.visibility)
  } else {
    const questionnaires = doc.questionnaires
    if (
      typeof questionnaires === 'object' &&
      questionnaires !== null &&
      !Array.isArray(questionnaires)
    ) {
      const seen = new Set<string>()
      for (const items of Object.values(questionnaires as Dict)) {
        for (const entry of parseVisibilityEntries(items)) {
          if (!seen.has(entry.section_id)) {
            seen.add(entry.section_id)
            entries.push(entry)
          }
        }
      }
    }
  }
  return entries.length > 0 ? entries : null
}

function parseQuestionnaireVisibilityEntries(items: unknown): QuestionnaireVisibilityEntry[] {
  const entries: QuestionnaireVisibilityEntry[] = []
  for (const item of arr(items)) {
    const entry = rec(item)
    if (typeof entry.questionnaire === 'string' && typeof entry.isVisible === 'boolean') {
      entries.push({ questionnaire_id: entry.questionnaire, is_visible: entry.isVisible })
    }
  }
  return entries
}

// Parses the same JSON blob as parseLayoutJson (the layout custom field's
// stored value) for its per-questionnaire `{questionnaire, isVisible}`
// entries: the app's `questionnaires` array, falling back to the
// superseded `questionnaireVisibility` key. The v2 layout shape's
// `questionnaires` object is not an array, so it reads as absent here. No
// valid entries is null, as in the app.
export function parseQuestionnaireVisibilityJson(raw: unknown): QuestionnaireVisibilityEntry[] | null {
  const doc = parseLayoutDoc(raw)
  if (doc === null) {
    return null
  }
  const items = Array.isArray(doc.questionnaires) ? doc.questionnaires : doc.questionnaireVisibility
  const entries = parseQuestionnaireVisibilityEntries(items)
  return entries.length > 0 ? entries : null
}

// A merged section counts as part of the default layout only if at least one
// of its (unioned, across questionnaires) questions is supported — a section
// made entirely of hidden/unsupported questions never shows up as an empty
// draggable row.
function hasSupportedQuestion(section: MergedSection): boolean {
  return section.questions.some((q) => !UNSUPPORTED_EVIDENCE_TYPES.has(q.evidence_type))
}

// Apply a saved layout to the live merged sections.
//
// Stale section ids (no longer live, or live but entirely unsupported) are
// dropped; live sections missing from the saved layout are appended visible.
export function mergeLayout(
  saved: LayoutEntry[] | null,
  sections: MergedSection[],
): LayoutEntry[] {
  const liveIds = sections.filter(hasSupportedQuestion).map((s) => s.id)
  if (!saved || saved.length === 0) {
    return liveIds.map((sid) => ({ section_id: sid, is_visible: true }))
  }
  const liveIdSet = new Set(liveIds)
  const validSaved = saved.filter((e) => liveIdSet.has(e.section_id))
  const savedIds = new Set(validSaved.map((e) => e.section_id))
  return validSaved.concat(
    liveIds
      .filter((sid) => !savedIds.has(sid))
      .map((sid) => ({ section_id: sid, is_visible: true })),
  )
}

// Merge every attached (and allowed) questionnaire's sections into one flat,
// deduplicated list. A section id repeated across questionnaires merges into
// one entry — an accepted tradeoff of not namespacing section ids — with its
// questions unioned in questionnaire-array order and the *last* occurrence's
// title/description winning on a conflict (a rare tenant-config edge case,
// not worth more than "last wins"). Order is first-occurrence position.
export function buildMergedSections(questionnaires: Questionnaire[]): MergedSection[] {
  const bySectionId = new Map<string, MergedSection>()
  const order: string[] = []
  for (const questionnaire of questionnaires) {
    for (const section of questionnaire.sections) {
      const taggedQuestions = section.questions.map((q) => ({
        ...q,
        questionnaire_id: questionnaire.id,
      }))
      const existing = bySectionId.get(section.id)
      if (existing) {
        existing.title = section.title
        existing.description = section.description
        existing.questions = existing.questions.concat(taggedQuestions)
      } else {
        bySectionId.set(section.id, {
          id: section.id,
          title: section.title,
          description: section.description,
          questions: taggedQuestions,
        })
        order.push(section.id)
      }
    }
  }
  return order.map((id) => bySectionId.get(id)!)
}

// Resolve a live layout to the full set of question ids it currently hides —
// a hidden section hides every question across every questionnaire that
// contributes a section with that id; a visible section can still hide
// individual questions via `hidden_question_ids`.
export function computeHiddenQuestionIds(
  layout: LayoutEntry[],
  sections: MergedSection[],
): Set<string> {
  const bySectionId = new Map(sections.map((s) => [s.id, s]))
  const hidden = new Set<string>()
  for (const entry of layout) {
    const section = bySectionId.get(entry.section_id)
    if (!section) {
      continue
    }
    if (!entry.is_visible) {
      for (const question of section.questions) {
        hidden.add(question.id)
      }
      continue
    }
    for (const questionId of entry.hidden_question_ids ?? []) {
      hidden.add(questionId)
    }
  }
  return hidden
}

export const NO_MATCHING_QUESTIONNAIRES_MESSAGE = 'No matching questionnaires found.'

// Filter attached questionnaires down to the allow-listed set. An empty
// allow list means "show everything." Reports which questionnaires were
// excluded so their saved layout entries can round-trip through a save
// untouched (see `preserveExcludedLayout`).
export function applyQuestionnaireAllowList(
  attached: Questionnaire[],
  allowedIds: string[],
): { included: Questionnaire[]; excluded: Questionnaire[] } {
  if (allowedIds.length === 0) {
    return { included: attached, excluded: [] }
  }
  const allowed = new Set(allowedIds)
  return {
    included: attached.filter((q) => allowed.has(q.id)),
    excluded: attached.filter((q) => !allowed.has(q.id)),
  }
}

// Union a live (included-questionnaire) layout with any persisted entries
// that belong only to questionnaires currently excluded by the allow list —
// so restricting the allow list, saving, then widening it again doesn't lose
// the excluded questionnaires' remembered order/visibility.
export function preserveExcludedLayout(
  liveLayout: LayoutEntry[],
  existingLayoutRaw: unknown,
  includedQuestionnaires: Questionnaire[],
  excludedQuestionnaires: Questionnaire[],
): LayoutEntry[] {
  if (excludedQuestionnaires.length === 0) {
    return liveLayout
  }
  const existing = parseLayoutJson(existingLayoutRaw) ?? []
  const includedSectionIds = new Set(
    includedQuestionnaires.flatMap((q) => q.sections.map((s) => s.id)),
  )
  const excludedSectionIds = new Set(
    excludedQuestionnaires.flatMap((q) => q.sections.map((s) => s.id)),
  )
  const preserved = existing.filter(
    (entry) => !includedSectionIds.has(entry.section_id) && excludedSectionIds.has(entry.section_id),
  )
  return liveLayout.concat(preserved)
}

export interface ComposeCardInput {
  useCase: UseCaseInfo
  attachedQuestionnaires: Questionnaire[]
  allowedQuestionnaireIds: string[]
  evidences: unknown[]
  cardStatus: unknown
  publishedStatusValue: string
  layoutRaw: unknown
  layoutFieldId: string | null
  // Host-supplied fallback (defaultLayout), same shape as the
  // persisted layout value — used only when that field has no saved value
  // yet. Malformed JSON here degrades to plain discovery order (via
  // parseLayoutJson's existing never-throw behavior), same as a malformed
  // saved value would.
  defaultLayoutRaw: unknown
  customFieldDefs: CustomFieldDef[]
  customFieldValues: unknown[]
  configuredCustomFieldNames: string[]
  useCaseFieldNames: string[]
}

export function composeCard(input: ComposeCardInput): CardResponse {
  const { included, excluded } = applyQuestionnaireAllowList(
    input.attachedQuestionnaires,
    input.allowedQuestionnaireIds,
  )
  const mismatchError =
    input.attachedQuestionnaires.length > 0 && included.length === 0
      ? NO_MATCHING_QUESTIONNAIRES_MESSAGE
      : null

  const sections = buildMergedSections(included)
  const answers = normalizeAnswers(input.evidences, included)
  // Default fallback rules match the Credo AI app's: saved sections win over the
  // default's sections on their own; the default's questionnaire visibility
  // applies only when the saved field holds neither sections nor
  // questionnaires, so a saved layout is never partly overridden by it.
  const savedLayout = parseLayoutJson(input.layoutRaw)
  const savedQuestionnaireVisibility = parseQuestionnaireVisibilityJson(input.layoutRaw)
  const layout = mergeLayout(savedLayout ?? parseLayoutJson(input.defaultLayoutRaw), sections)

  const savedFieldHasValue = savedLayout !== null || savedQuestionnaireVisibility !== null
  const questionnaireVisibility = buildQuestionnaireVisibility(
    savedQuestionnaireVisibility ??
      (savedFieldHasValue ? null : parseQuestionnaireVisibilityJson(input.defaultLayoutRaw)),
    included.map((q) => q.id),
  )

  const status =
    input.cardStatus === null || input.cardStatus === undefined
      ? null
      : String(input.cardStatus)

  const customFields = resolveConfiguredCustomFields(
    input.customFieldDefs,
    input.customFieldValues,
    input.configuredCustomFieldNames,
  )
  const previewMetadataRows = buildMetadataRows(input.useCase.attributes, input.useCaseFieldNames)

  return {
    use_case: input.useCase,
    questionnaires: input.attachedQuestionnaires.map(summarizeQuestionnaire),
    included_questionnaires: included,
    sections,
    excluded_questionnaire_names: excluded.map((q) => q.name),
    mismatch_error: mismatchError,
    answers,
    custom_fields: customFields,
    preview_metadata_rows: previewMetadataRows,
    card_mode: deriveCardMode(answers),
    card_status: status,
    is_published: status === input.publishedStatusValue,
    layout,
    layout_persistence_available: input.layoutFieldId !== null,
    questionnaire_visibility: questionnaireVisibility,
  }
}

// Build the integration-service PATCH body.
//
// Only supported, non-hidden answers are sent — the PATCH upserts per
// included question, and omitting a question leaves its upstream evidence
// untouched. Empty *scalars* are omitted (they go through the delete-on-clear
// path instead — see lib/clear-on-save.ts); empty *arrays* are always
// included as `value: []`, since that's what clears a multi-select upstream
// on its own.
export function buildSavePayload(input: {
  sections: MergedSection[]
  // The full live form-values object (question ids and `cf:<id>`-prefixed
  // custom-field keys alike) — this function is the single place that
  // decides what's includable, so callers don't pre-filter and risk
  // double-filtering (e.g. skipping an emptied array before it gets here).
  answers: Record<string, unknown>
  hiddenQuestionIds: Set<string>
  layoutToPersist: LayoutEntry[] | null
  layoutFieldId: string | null
  // Persisted into the same layout custom field's JSON blob, under a
  // separate `questionnaires` key — only written when a layout is
  // also being persisted (there is no custom field to write into otherwise).
  questionnaireVisibilityToPersist?: QuestionnaireVisibilityEntry[] | null
  // Every currently-configured custom field (customFieldNames,
  // resolved fresh by the caller) — always written, even back to `null` when
  // cleared, unlike questionnaire answers (omitted when empty, cleared via
  // the delete-on-clear path instead).
  customFields: CustomFieldDef[]
  user: string
}): SavePayload {
  const questionOrigin = new Map<string, { questionnaire_id: string; section_id: string }>()
  const supported = new Set<string>()
  for (const section of input.sections) {
    for (const question of section.questions) {
      questionOrigin.set(question.id, {
        questionnaire_id: question.questionnaire_id,
        section_id: section.id,
      })
      if (!UNSUPPORTED_EVIDENCE_TYPES.has(question.evidence_type)) {
        supported.add(question.id)
      }
    }
  }

  const questions: SaveQuestionEntry[] = []
  for (const [questionId, value] of Object.entries(input.answers)) {
    if (!supported.has(questionId) || input.hiddenQuestionIds.has(questionId)) {
      continue
    }
    const origin = questionOrigin.get(questionId)
    if (!origin) {
      continue
    }
    // `undefined` is an unanswered question with no stored evidence —
    // String() would send it as the literal "undefined".
    if (value === null || value === undefined || value === '') {
      continue
    }
    const cleanValue: string | string[] = Array.isArray(value)
      ? value.filter((v) => v !== null && v !== undefined && v !== '').map(String)
      : String(value)
    questions.push({
      questionnaire_id: origin.questionnaire_id,
      section_id: origin.section_id,
      question_id: questionId,
      value: cleanValue,
    })
  }

  const payload: SavePayload = { questions, user: input.user }
  const customFieldEntries: { custom_field_id: string; value: unknown }[] = []

  if (input.layoutToPersist !== null && input.layoutFieldId !== null) {
    // Same JSON as the Credo AI app's card writes, so a card configured in one
    // keeps its hidden sections, questions and questionnaires in the other.
    // A section with any hidden question lists every supported question with
    // its flag (the app's shape); hidden ids with no live row (a preserved,
    // allow-list-excluded section) are appended so they aren't lost.
    const supportedIdsBySection = new Map(
      input.sections.map((s) => [
        s.id,
        s.questions.filter((q) => !UNSUPPORTED_EVIDENCE_TYPES.has(q.evidence_type)).map((q) => q.id),
      ]),
    )
    customFieldEntries.push({
      custom_field_id: input.layoutFieldId,
      value: JSON.stringify({
        visibility: input.layoutToPersist.map((e) => {
          const hidden = new Set(e.hidden_question_ids ?? [])
          if (hidden.size === 0) {
            return { section: e.section_id, isVisible: e.is_visible }
          }
          const rowIds = supportedIdsBySection.get(e.section_id) ?? []
          const questionIds = rowIds.concat([...hidden].filter((id) => !rowIds.includes(id)))
          return {
            section: e.section_id,
            isVisible: e.is_visible,
            questions: questionIds.map((id) => ({ question: id, isVisible: !hidden.has(id) })),
          }
        }),
        questionnaires: (input.questionnaireVisibilityToPersist ?? []).map((e) => ({
          questionnaire: e.questionnaire_id,
          isVisible: e.is_visible,
        })),
      }),
    })
  }

  for (const def of input.customFields) {
    const value = input.answers[customFieldFormKey(def.id)]
    customFieldEntries.push({
      custom_field_id: def.id,
      value: value === undefined || value === '' ? null : value,
    })
  }

  if (customFieldEntries.length > 0) {
    payload.custom_fields = customFieldEntries
  }

  return payload
}

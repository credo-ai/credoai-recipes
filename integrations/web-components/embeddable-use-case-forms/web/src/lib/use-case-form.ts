import type { PreviewAnswer, QuestionnairePreview } from '../api/platform'
import { isSupported } from './render-rules'
import type { Question, Questionnaire, Section } from '../types'

// Pure logic behind the three use-case forms (intake / create / edit).
// Everything here is I/O-free and unit-tested; the components are rendering
// on top of it.
//
// Form value shape mirrors Credo AI's hosted web components, because it is also
// the shape the integration service's create/update payload is built from:
//
//   {
//     name, description,
//     customFields: { <customFieldId>: value },
//     questions: { <questionnaireId>: { <sectionId>: { <questionId>: value } } }
//   }

// What a question control can hold. Every control render-rules produces
// (textarea/input/number/date/radio/select/checkbox-group/chip-select) yields
// a string or an array of strings — never a boolean or a number. Custom
// fields are the looser case (checkbox fields are boolean), so they stay
// `unknown`.
export type QuestionAnswer = string | string[]

export interface UseCaseFormValues {
  name: string
  description: string
  customFields: Record<string, unknown>
  questions: Record<string, Record<string, Record<string, QuestionAnswer>>>
}

export interface SubmittedQuestion {
  questionnaireId: string
  sectionId: string
  questionId: string
  value: QuestionAnswer
}

export interface SubmittedCustomField {
  customFieldId: string
  value: unknown
}

export interface UseCaseSubmission {
  name: string
  description: string
  customFields: SubmittedCustomField[]
  questions: SubmittedQuestion[]
}

export const QUESTION_FIELD_ROOT = 'questions'
export const CUSTOM_FIELD_ROOT = 'customFields'

export function questionFieldName(
  questionnaireId: string,
  sectionId: string,
  questionId: string,
): string {
  return `${QUESTION_FIELD_ROOT}.${questionnaireId}.${sectionId}.${questionId}`
}

export function customFieldName(customFieldId: string): string {
  return `${CUSTOM_FIELD_ROOT}.${customFieldId}`
}

// `file` and `user` questions have no control (see render-rules) and are
// excluded from both rendering and submission.
export function supportedQuestions(section: Section): Question[] {
  return section.questions.filter(isSupported)
}

function isEmptySubmissionValue(value: unknown): boolean {
  return value === undefined || value === null || value === ''
}

// An emptied multi-select must survive as `[]` so the upsert clears it
// upstream; an empty scalar is simply omitted. Same asymmetry the
// transparency card's save path documents.
export function parseUseCaseForm(values: Partial<UseCaseFormValues>): UseCaseSubmission {
  const customFields = Object.entries(values.customFields ?? {})
    .filter(([, value]) => !isEmptySubmissionValue(value))
    .map(([customFieldId, value]) => ({ customFieldId, value }))

  const questions = Object.entries(values.questions ?? {}).flatMap(
    ([questionnaireId, sections]) =>
      Object.entries(sections ?? {}).flatMap(([sectionId, sectionQuestions]) =>
        Object.entries(sectionQuestions ?? {})
          .filter(([, value]) => !isEmptySubmissionValue(value))
          .map(([questionId, value]) => ({
            questionnaireId,
            sectionId,
            questionId,
            value,
          })),
      ),
  )

  return {
    name: values.name ?? '',
    description: values.description ?? '',
    customFields,
    questions,
  }
}

// Flattens the form's nested questions record into the preview endpoint's
// payload, for ONE questionnaire. The preview is evaluated per questionnaire
// and needs every answer for that questionnaire in a single request —
// splitting them across calls loses cross-section trigger context.
export function toPreviewAnswers(
  values: Partial<UseCaseFormValues>,
  questionnaireId: string,
): PreviewAnswer[] {
  const sections = values.questions?.[questionnaireId] ?? {}
  return Object.entries(sections).flatMap(([sectionId, sectionQuestions]) =>
    Object.entries(sectionQuestions ?? {}).map(([questionId, value]) => ({
      section_id: sectionId,
      question_id: questionId,
      value,
    })),
  )
}

// A question with no preview entry is visible: absence means "the engine had
// nothing to say", not "hidden". Only an explicit `visible: false` hides it.
// A conditional (`hidden`) question is the reverse: it shows only once the
// preview explicitly marks it visible, so it stays out of the static intake
// form (no preview), and doesn't flash in before the first preview arrives.
export function isQuestionVisible(
  preview: QuestionnairePreview | undefined,
  sectionId: string,
  question: Question,
): boolean {
  const section = preview?.sections?.find((s) => s.id === sectionId)
  const visible = section?.questions?.find((q) => q.id === question.id)?.visible
  return question.hidden ? visible === true : visible !== false
}

// A section renders when at least one of its supported questions is visible.
export function visibleQuestions(
  section: Section,
  preview: QuestionnairePreview | undefined,
): Question[] {
  return supportedQuestions(section).filter((q) =>
    isQuestionVisible(preview, section.id, q),
  )
}

export function visibleSections(
  questionnaire: Questionnaire,
  preview: QuestionnairePreview | undefined,
): Section[] {
  return questionnaire.sections.filter(
    (section) => visibleQuestions(section, preview).length > 0,
  )
}

// Required-and-visible questions with no answer. Unlike the transparency
// card — which writes evidences and only warns — these forms are the
// questionnaire submission flow, so this gates Submit/Update.
export function missingRequiredQuestions(
  values: Partial<UseCaseFormValues>,
  questionnaires: Questionnaire[],
  previews: Record<string, QuestionnairePreview | undefined>,
): string[] {
  const missing: string[] = []
  for (const questionnaire of questionnaires) {
    const preview = previews[questionnaire.id]
    for (const section of questionnaire.sections) {
      for (const question of visibleQuestions(section, preview)) {
        if (!question.required) {
          continue
        }
        const value = values.questions?.[questionnaire.id]?.[section.id]?.[question.id]
        const isEmpty = Array.isArray(value)
          ? value.length === 0
          : isEmptySubmissionValue(value)
        if (isEmpty) {
          missing.push(questionFieldName(questionnaire.id, section.id, question.id))
        }
      }
    }
  }
  return missing
}

interface EvidenceRecord {
  questionId: string
  value: unknown
}

// Pre-fills the edit form: existing answers keyed back into the nested form
// shape, with every supported question present so react-hook-form treats a
// cleared field as a change rather than an untouched absence.
//
// An unanswered question is seeded '' even when it's a multi-select, as
// the Credo AI app does: parseUseCaseForm drops '', so an untouched multi-select isn't
// submitted as [] (only one the user actually emptied is). An existing answer
// is kept as the API returned it, so an untouched one is sent back unchanged.
export function buildFormDefaults(args: {
  name: string
  description: string
  questionnaires: Questionnaire[]
  evidences: EvidenceRecord[]
  customFieldValues: Record<string, unknown>
}): UseCaseFormValues {
  const byQuestionId = new Map(args.evidences.map((e) => [e.questionId, e.value]))

  const questions: UseCaseFormValues['questions'] = {}
  for (const questionnaire of args.questionnaires) {
    const sections: Record<string, Record<string, QuestionAnswer>> = {}
    for (const section of questionnaire.sections) {
      const answers: Record<string, QuestionAnswer> = {}
      for (const question of supportedQuestions(section)) {
        answers[question.id] = (byQuestionId.get(question.id) as QuestionAnswer | undefined) ?? ''
      }
      sections[section.id] = answers
    }
    questions[questionnaire.id] = sections
  }

  return {
    name: args.name,
    description: args.description,
    customFields: { ...args.customFieldValues },
    questions,
  }
}

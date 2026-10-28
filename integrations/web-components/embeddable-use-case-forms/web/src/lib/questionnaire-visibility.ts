import type { MergedSection, QuestionnaireVisibilityEntry } from '../types'

// Pure functions backing the Setup tab's whole-questionnaire visibility
// toggle. Unlike the section-level layout (LayoutEntry), this is purely a
// display-time override: hiding a questionnaire never mutates any section's
// or question's own hidden state (lib/layout.ts) — turning it back on
// reverts every one of its questions to exactly whatever state that toggle
// already held.
//
// Every merged question already carries its origin questionnaire_id
// (buildMergedSections), so — unlike a data model where sections belong 1:1
// to a questionnaire — resolving which questions a hidden questionnaire
// affects needs no section→questionnaire lookup: it's a direct per-question
// filter, and correctly handles a section shared across questionnaires by
// hiding only the questions that came from the hidden one.

export function buildDefaultQuestionnaireVisibility(
  questionnaireIds: string[],
): QuestionnaireVisibilityEntry[] {
  return questionnaireIds.map((id) => ({ questionnaire_id: id, is_visible: true }))
}

// Reconcile saved entries against the current questionnaire set: keep saved
// entries whose ids still exist, append a default-visible entry for any
// questionnaire not yet represented (newly attached since the last save).
export function buildQuestionnaireVisibility(
  saved: QuestionnaireVisibilityEntry[] | null,
  questionnaireIds: string[],
): QuestionnaireVisibilityEntry[] {
  const liveIds = new Set(questionnaireIds)
  const validSaved = (saved ?? []).filter((entry) => liveIds.has(entry.questionnaire_id))
  const savedIds = new Set(validSaved.map((entry) => entry.questionnaire_id))
  return validSaved.concat(
    questionnaireIds
      .filter((id) => !savedIds.has(id))
      .map((id) => ({ questionnaire_id: id, is_visible: true })),
  )
}

// When the allow list excludes a questionnaire, keep its saved visibility
// entry around unmodified so it round-trips back into storage on the next
// save instead of being silently dropped — mirrors preserveExcludedLayout.
export function collectPreservedQuestionnaireVisibility(
  saved: QuestionnaireVisibilityEntry[] | null,
  excludedQuestionnaireIds: string[],
): QuestionnaireVisibilityEntry[] {
  if (excludedQuestionnaireIds.length === 0) {
    return []
  }
  const excluded = new Set(excludedQuestionnaireIds)
  return (saved ?? []).filter((entry) => excluded.has(entry.questionnaire_id))
}

export function toggleQuestionnaireVisibility(
  visibility: QuestionnaireVisibilityEntry[],
  questionnaireId: string,
  isVisible: boolean,
): QuestionnaireVisibilityEntry[] {
  return visibility.map((entry) =>
    entry.questionnaire_id === questionnaireId ? { ...entry, is_visible: isVisible } : entry,
  )
}

// Question ids hidden purely because their origin questionnaire is toggled
// off — a display-time-only overlay, independent of each question's own
// LayoutEntry.hidden_question_ids (computeHiddenQuestionIds in compose.ts).
export function computeQuestionnaireHiddenQuestionIds(
  sections: MergedSection[],
  questionnaireVisibility: QuestionnaireVisibilityEntry[],
): Set<string> {
  const hiddenQuestionnaireIds = new Set(
    questionnaireVisibility.filter((entry) => !entry.is_visible).map((entry) => entry.questionnaire_id),
  )
  const hidden = new Set<string>()
  if (hiddenQuestionnaireIds.size === 0) {
    return hidden
  }
  for (const section of sections) {
    for (const question of section.questions) {
      if (hiddenQuestionnaireIds.has(question.questionnaire_id)) {
        hidden.add(question.id)
      }
    }
  }
  return hidden
}

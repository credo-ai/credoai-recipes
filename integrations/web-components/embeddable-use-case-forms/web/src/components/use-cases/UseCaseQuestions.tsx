import type { QuestionnairePreview } from '../../api/platform'
import {
  questionFieldName,
  visibleQuestions,
  visibleSections,
} from '../../lib/use-case-form'
import type { Questionnaire } from '../../types'
import { QuestionField } from '../QuestionField'
import { FormSection } from './FormLayout'

// Renders one or more questionnaires' sections and questions.
//
// The same component serves the static and the conditional case: pass no
// preview and every supported question renders (the intake form's behaviour);
// pass one and the engine's visibility decides (create/edit). That is the only
// difference between them, so there is one component rather than two.
export function UseCaseQuestions({
  questionnaires,
  previews,
}: {
  questionnaires: Questionnaire[]
  previews?: Record<string, QuestionnairePreview | undefined>
}) {
  const rendered = questionnaires
    .map((questionnaire) => ({
      questionnaire,
      sections: visibleSections(questionnaire, previews?.[questionnaire.id]),
    }))
    .filter(({ sections }) => sections.length > 0)

  if (rendered.length === 0) {
    return null
  }

  return (
    <FormSection title="Questions">
      <div className="flex flex-col gap-8">
        {rendered.map(({ questionnaire, sections }) => (
          <div key={questionnaire.id} data-questionnaire-id={questionnaire.id}>
            {questionnaires.length > 1 ? (
              <p className="mb-3 text-[14px] font-bold text-gray-900">{questionnaire.name}</p>
            ) : null}
            <div className="flex flex-col gap-6">
              {sections.map((section) => (
                <div key={section.id} data-section-id={section.id}>
                  <p className="text-[13px] font-bold text-gray-900">{section.title}</p>
                  {section.description ? (
                    <p className="mt-0.5 text-[12px] text-gray-500">{section.description}</p>
                  ) : null}
                  <div className="mt-3 flex flex-col gap-4">
                    {visibleQuestions(section, previews?.[questionnaire.id]).map((question) => (
                      <QuestionField
                        key={question.id}
                        question={question}
                        name={questionFieldName(questionnaire.id, section.id, question.id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </FormSection>
  )
}

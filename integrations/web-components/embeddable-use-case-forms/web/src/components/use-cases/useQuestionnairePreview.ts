import { useEffect, useRef, useState } from 'react'

import type { PlatformClient, QuestionnairePreview } from '../../api/platform'
import { type UseCaseFormValues, toPreviewAnswers } from '../../lib/use-case-form'

const THROTTLE_MS = 500

// Keeps the conditional-questionnaire preview in step with the answers.
//
// Every answer for a questionnaire goes in ONE request: the engine evaluates
// triggers across sections, so splitting a questionnaire's answers over
// several calls loses the context that decides what's visible.
//
// Trailing-edge throttled, and the timer lives in a ref rather than being
// recreated per render — recreating it on every dependency change cancels the
// pending trailing call, which is how a debounce silently never fires.
export function useQuestionnairePreview(
  platform: PlatformClient | null,
  questionnaireIds: string[],
  values: Partial<UseCaseFormValues>,
  enabled: boolean,
): Record<string, QuestionnairePreview | undefined> {
  const [previews, setPreviews] = useState<Record<string, QuestionnairePreview | undefined>>({})
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const idsKey = questionnaireIds.join(',')
  // Only the answers drive re-evaluation; the rest of the form does not.
  const answersKey = JSON.stringify(values.questions ?? {})

  // Initial state: what's visible before anything is answered.
  useEffect(() => {
    if (!platform || !enabled || questionnaireIds.length === 0) {
      return
    }
    let cancelled = false
    void Promise.all(
      questionnaireIds.map((id) =>
        platform
          .getQuestionnairePreview(id)
          .then((preview) => [id, preview] as const)
          .catch(() => [id, undefined] as const),
      ),
    ).then((entries) => {
      if (!cancelled) {
        setPreviews(Object.fromEntries(entries))
      }
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform, enabled, idsKey])

  // Re-evaluation as answers change.
  useEffect(() => {
    if (!platform || !enabled || questionnaireIds.length === 0) {
      return
    }
    if (timerRef.current) {
      clearTimeout(timerRef.current)
    }
    timerRef.current = setTimeout(() => {
      void Promise.all(
        questionnaireIds.map((id) =>
          platform
            .updateQuestionnairePreview(id, toPreviewAnswers(values, id))
            .then((preview) => [id, preview] as const)
            // A failed re-evaluation keeps the previous preview rather than
            // collapsing the form to "everything visible" mid-edit.
            .catch(() => null),
        ),
      ).then((entries) => {
        setPreviews((prev) => {
          const next = { ...prev }
          for (const entry of entries) {
            if (entry) {
              next[entry[0]] = entry[1]
            }
          }
          return next
        })
      })
    }, THROTTLE_MS)

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform, enabled, idsKey, answersKey])

  return previews
}

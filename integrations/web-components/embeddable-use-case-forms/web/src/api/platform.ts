import axios from 'axios'

import type { CredoStatus, CredoTokens, QuestionnaireSummary } from '../types'
import { CredoApiError } from './credo'
import type { TokenSource } from './tokens'

// The two platform API (`/api/v2/{tenant}`) reads the use-case forms need
// that CredoClient can't do, both because no SDK wraps them:
//
// - The questionnaire *preview* endpoints: the conditional-questionnaire
//   engine. POST the answers so far, get back which sections and questions
//   are visible.
// - The questionnaire *list*, for the intake/create forms' picker. The public
//   surface has a list resource, but it exposes only `isPublished`, which
//   does not reflect whether a questionnaire is offered at creation time —
//   filtering on it can empty the picker. The flags that do,
//   `metadata.archived` and `metadata.show_on_create`, exist only here.
//
// Everything else these forms need (questionnaire content, custom-field
// definitions and values, use case reads, create/update) has a CredoClient
// method already and is not duplicated here.

export interface QuestionPreview {
  id: string
  visible: boolean
  triggered?: boolean
}

export interface SectionPreview {
  id: string
  questions: QuestionPreview[]
}

export interface QuestionnairePreview {
  sections: SectionPreview[]
}

// A questionnaire as the picker needs it, plus the two flags that decide
// whether it is offered at all.
interface QuestionnaireListEntry {
  id?: unknown
  attributes?: {
    key?: unknown
    name?: unknown
    version?: unknown
    metadata?: { archived?: unknown; show_on_create?: unknown }
  }
}

// Offered at creation time = not archived, and explicitly flagged to show.
// `show_on_create` must be exactly true: an absent flag is not an opt-in.
export function parseCreatableQuestionnaires(raw: unknown[]): QuestionnaireSummary[] {
  return raw
    .flatMap((item) => {
      const entry = item as QuestionnaireListEntry
      const metadata = entry.attributes?.metadata
      if (metadata?.archived === true || metadata?.show_on_create !== true) {
        return []
      }
      const id = entry.id
      const name = entry.attributes?.name
      if (typeof id !== 'string' || typeof name !== 'string') {
        return []
      }
      const key = entry.attributes?.key
      const version = entry.attributes?.version
      return [
        {
          id,
          key: typeof key === 'string' ? key : null,
          name,
          version: typeof version === 'number' ? version : null,
        },
      ]
    })
    .sort((a, b) => a.name.localeCompare(b.name))
}

export interface PreviewAnswer {
  section_id: string
  question_id: string
  value: unknown
}

export class PlatformClient {
  private config: CredoStatus
  private tokenSource: TokenSource
  private tokens: CredoTokens | null = null
  private refreshPromise: Promise<CredoTokens> | null = null

  constructor(config: CredoStatus, tokenSource: TokenSource) {
    this.config = config
    this.tokenSource = tokenSource
  }

  private refreshTokens(): Promise<CredoTokens> {
    this.refreshPromise ??= this.tokenSource()
      .then((tokens) => {
        this.tokens = tokens
        return tokens
      })
      .finally(() => {
        this.refreshPromise = null
      })
    return this.refreshPromise
  }

  // Same refresh-and-retry-once-on-401 contract as CredoClient.
  private async withRetry<T>(run: (tokens: CredoTokens) => Promise<T>): Promise<T> {
    const tokens = this.tokens ?? (await this.refreshTokens())
    try {
      return await run(tokens)
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        this.tokens = null
        return run(await this.refreshTokens())
      }
      throw err
    }
  }

  private url(path: string): string {
    return `${this.config.private_api_base_url}/api/v2/${this.config.tenant}${path}`
  }

  private async request<T>(
    method: 'get' | 'post',
    path: string,
    options: { params?: Record<string, string>; body?: unknown } = {},
  ): Promise<T> {
    try {
      return await this.withRetry(async (tokens) => {
        const { data } = await axios.request<T>({
          method,
          url: this.url(path),
          params: options.params,
          data: options.body,
          headers: {
            Authorization: `Bearer ${tokens.integration_api_token}`,
            Accept: 'application/vnd.api+json',
            ...(options.body ? { 'Content-Type': 'application/json' } : {}),
          },
        })
        return data
      })
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : undefined
      const body = axios.isAxiosError(err) ? err.response?.data : undefined
      throw new CredoApiError(
        `${method.toUpperCase()} ${path} failed${status ? `: ${status}` : ''}`,
        status,
        body,
      )
    }
  }

  // The picker's questionnaire list. `filter[target]` is JSON:API filter
  // syntax — a bare `target` param 400s in current platform versions,
  // the same change that caught `/custom_fields`.
  async listCreatableQuestionnaires(target = 'use_case'): Promise<QuestionnaireSummary[]> {
    const doc = await this.request<{ data?: unknown[] }>('get', '/questionnaires', {
      params: { 'filter[target]': target, 'page[limit]': '100' },
    })
    return parseCreatableQuestionnaires(Array.isArray(doc.data) ? doc.data : [])
  }

  // The conditional-questionnaire engine. GET returns the visibility state for
  // an empty questionnaire; POST re-evaluates it against the answers so far.
  async getQuestionnairePreview(questionnaireId: string): Promise<QuestionnairePreview> {
    return this.request('get', `/questionnaires/${questionnaireId}/preview`)
  }

  async updateQuestionnairePreview(
    questionnaireId: string,
    answers: PreviewAnswer[],
  ): Promise<QuestionnairePreview> {
    return this.request('post', `/questionnaires/${questionnaireId}/preview`, {
      body: { questions: answers },
    })
  }
}

import axios from 'axios'
import {
  createCredoAIClient,
  type CredoAIClient,
  type CustomFieldDefinition,
  type CustomFieldValue,
  type EvidenceResponse,
  type ModelResponse,
  type QuestionnaireResponse,
  type UseCaseResponse,
  type VendorResponse,
} from '@credo-ai/sdk'

import type { CredoStatus, CredoTokens } from '../types'
import type { SavePayload } from '../lib/compose'
import type { UseCaseSubmission } from '../lib/use-case-form'
import type { TokenSource } from './tokens'

// Browser-side client for the Credo AI integration service. The token service only
// mints tokens; every data call is made directly from here, exactly as a
// customer's own frontend would make them:
//
// - Integration service (`/api/v1/integration/...`): every data call — use
//   case reads/writes, connected models/vendors, questionnaire reads,
//   evidence reads/deletes, and custom-field definitions/values — goes through `@credo-ai/sdk`, constructed
//   per-call with the short-lived bearer token below — never the raw API
//   key, which stays on the backend. The SDK returns `{data, error}` and
//   never throws; `unwrapSdk` converts that back into the same thrown
//   `CredoApiError` the raw-HTTP methods use, and the adapters below
//   (`useCaseToJsonApiDoc`/`questionnaireToJsonApiDoc`/
//   `evidenceToJsonApiDoc`/`customFieldDefToJsonApiDoc`/
//   `customFieldValueToJsonApiDoc`) reshape its flat, camelCased responses back into
//   the JSON:API-shaped, snake_case documents `compose.ts`'s parsers already
//   expect — so this file is the only one that needs to know the wire shape
//   changed.

const PAGE_LIMIT = 100

export class CredoApiError extends Error {
  status?: number
  body?: unknown

  constructor(message: string, status?: number, body?: unknown) {
    super(message)
    this.name = 'CredoApiError'
    this.status = status
    this.body = body
  }
}

function isUnauthorized(err: unknown): boolean {
  if (axios.isAxiosError(err)) {
    return err.response?.status === 401
  }
  return err instanceof CredoApiError && err.status === 401
}

// SDK calls return `{data, error}` and never throw; unwrap into the same
// thrown CredoApiError the raw-HTTP methods use so callers (and withRetry's
// 401 detection) don't need to know which transport a given method uses.
function unwrapSdk<T>(
  context: string,
  result: { data: T | null; error: { status: number; body: unknown } | null },
): T {
  if (result.error) {
    throw new CredoApiError(`${context} failed: ${result.error.status}`, result.error.status, result.error.body)
  }
  return result.data as T
}

type Dict = Record<string, unknown>

function rec(v: unknown): Dict {
  return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Dict) : {}
}

// Bridges the SDK's flat UseCaseResponse — plus separate models/vendors list
// calls, since the public API has no `include=models,vendors` — back into
// the JSON:API document shape parseUseCase/parseConnectedEntityNames
// (compose.ts) already expect.
function useCaseToJsonApiDoc(
  useCase: UseCaseResponse,
  models: ModelResponse[],
  vendors: VendorResponse[],
): Dict {
  return {
    data: {
      id: useCase.id,
      attributes: {
        name: useCase.name,
        description: useCase.description ?? null,
        questionnaire_ids: useCase.questionnaireIds ?? [],
      },
      relationships: {
        models: { data: models.map((m) => ({ type: 'models', id: m.id })) },
        vendors: { data: vendors.map((v) => ({ type: 'vendors', id: v.id })) },
      },
    },
    included: [
      ...models.map((m) => ({ type: 'models', id: m.id, attributes: { name: m.name } })),
      ...vendors.map((v) => ({ type: 'vendors', id: v.id, attributes: { name: v.name } })),
    ],
  }
}

// SDK custom-field definition → the JSON:API item parseCustomFieldDefs /
// resolveFieldId (custom-fields.ts, compose.ts) already expect.
function customFieldDefToJsonApiDoc(def: CustomFieldDefinition): Dict {
  return {
    id: def.id,
    type: 'custom_fields',
    attributes: {
      name: def.name,
      element_type: def.elementType ?? 'input',
      options: def.options ?? [],
      multiple: def.multiple ?? false,
    },
  }
}

// SDK custom-field value → the inline-attributes JSON:API item
// readCustomFieldValue / resolveConfiguredCustomFields already expect.
function customFieldValueToJsonApiDoc(v: CustomFieldValue): Dict {
  return {
    id: `${v.entityId ?? ''}:${v.customFieldId}`,
    type: 'use_case_custom_fields',
    attributes: { custom_field_id: v.customFieldId, value: v.value ?? null },
  }
}

// Bridges the SDK's flat, camelCased QuestionnaireResponse back into the
// JSON:API document, snake_case-field shape parseQuestionnaire (compose.ts)
// already expects — same underlying data as the platform API's JSON:API
// wire format.
function questionnaireToJsonApiDoc(q: QuestionnaireResponse): Dict {
  return {
    data: {
      id: q.id,
      attributes: {
        name: q.name,
        key: q.key,
        version: q.version,
        sections: (q.sections ?? []).map((s) => ({
          id: s.id,
          title: s.title,
          description: s.description,
          questions: (s.questions ?? []).map((question) => ({
            id: question.id,
            question: question.question,
            description: question.description,
            evidence_type: question.evidenceType,
            select_options: question.selectOptions ?? [],
            multiple: question.multiple,
            required: question.required,
            hidden: question.hidden,
          })),
        })),
      },
    },
  }
}

// Bridges the SDK's flat, camelCased EvidenceResponse back into the
// JSON:API-shaped `{id, attributes: {label: {key}, data}}` entry
// normalizeAnswers (compose.ts)
// already expect — same underlying data as the platform API's JSON:API
// wire format.
function evidenceToJsonApiDoc(evidence: EvidenceResponse): Dict {
  return {
    id: evidence.id,
    attributes: {
      label: { key: evidence.questionId },
      data: evidence.data,
    },
  }
}

export class CredoClient {
  private config: CredoStatus
  private tokenSource: TokenSource
  private tokens: CredoTokens | null = null
  private refreshPromise: Promise<CredoTokens> | null = null
  // Questionnaires are immutable per id, so cache them for the page lifetime.
  private questionnaireCache = new Map<string, unknown>()

  constructor(config: CredoStatus, tokenSource: TokenSource) {
    this.config = config
    this.tokenSource = tokenSource
  }

  private async getTokens(): Promise<CredoTokens> {
    return this.tokens ?? this.refreshTokens()
  }

  // Concurrent callers share one in-flight token exchange.
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

  // Run a request; on a 401 (expired token) mint fresh tokens and retry once.
  private async withRetry<T>(run: (tokens: CredoTokens) => Promise<T>): Promise<T> {
    try {
      return await run(await this.getTokens())
    } catch (err) {
      if (isUnauthorized(err)) {
        this.tokens = null
        return run(await this.refreshTokens())
      }
      throw err
    }
  }

  // Built fresh per call rather than cached on the instance: construction is
  // pure/synchronous (no network I/O), and this sidesteps depending on
  // whether the SDK client supports updating its token in place after a
  // refresh, which isn't confirmed one way or the other from its types.
  private sdkClient(tokens: CredoTokens): CredoAIClient {
    return createCredoAIClient(this.config.tenant ?? undefined, {
      token: tokens.integration_api_token,
      baseURL: this.config.integration_api_base_url,
    })
  }

  // The public API has no `include=models,vendors` equivalent, so this is
  // three SDK calls (confirmed live) reassembled by useCaseToJsonApiDoc into
  // the shape a JSON:API `include` would return.
  async getUseCase(useCaseId: string): Promise<unknown> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const [useCase, models, vendors] = await Promise.all([
        client.useCases.get(useCaseId),
        client.useCases.models.list(useCaseId),
        client.useCases.vendors.list(useCaseId),
      ])
      const useCaseData = unwrapSdk(`GET use_cases/${useCaseId}`, useCase)
      const modelItems = unwrapSdk(`GET use_cases/${useCaseId}/models`, models).items
      const vendorItems = unwrapSdk(`GET use_cases/${useCaseId}/vendors`, vendors).items
      return useCaseToJsonApiDoc(useCaseData, modelItems, vendorItems)
    })
  }

  // Paginated with PAGE_LIMIT, like the other list reads.
  async getUseCaseEvidences(useCaseId: string): Promise<unknown[]> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const items: Dict[] = []
      let pageAfter: string | undefined
      for (;;) {
        const page = unwrapSdk(
          `GET use_cases/${useCaseId}/evidences`,
          await client.useCaseEvidence.list(useCaseId, { pageLimit: PAGE_LIMIT, pageAfter }),
        )
        items.push(...page.items.map(evidenceToJsonApiDoc))
        if (!page.pagination.hasMore || !page.pagination.nextCursor) {
          return items
        }
        pageAfter = page.pagination.nextCursor
      }
    })
  }

  // Deletes the evidence record for a question cleared back to empty — the
  // integration PATCH only upserts, so clearing a scalar answer needs this
  // explicit delete or the old value survives upstream.
  async deleteUseCaseEvidence(useCaseId: string, evidenceId: string): Promise<void> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      unwrapSdk(
        `DELETE use_cases/${useCaseId}/evidences/${evidenceId}`,
        await client.useCaseEvidence.delete(useCaseId, evidenceId),
      )
    })
  }

  // SDK 1.3.0 `useCases.customFields.list` returns a value row for every
  // custom field on the tenant (null when unset), each carrying its
  // `definition`. Confirmed against a live tenant.
  async getUseCaseCustomFields(useCaseId: string): Promise<unknown[]> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const values = unwrapSdk(
        `GET use_cases/${useCaseId}/custom_fields`,
        await client.useCases.customFields.list(useCaseId),
      )
      return values.map(customFieldValueToJsonApiDoc)
    })
  }

  // `customFields.list` takes `entityType` (not `filter[target]`) and is
  // unpaginated: its query type has no page params.
  async listCustomFieldDefs(target: 'use_case' | 'model' | 'vendor' = 'use_case'): Promise<unknown[]> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const page = unwrapSdk('GET custom_fields', await client.customFields.list({ entityType: target }))
      return page.items.map(customFieldDefToJsonApiDoc)
    })
  }

  // The public API returns the full questionnaire (sections, questions), just
  // flat/camelCased — see questionnaireToJsonApiDoc.
  async getQuestionnaire(questionnaireId: string): Promise<unknown> {
    const cached = this.questionnaireCache.get(questionnaireId)
    if (cached !== undefined) {
      return cached
    }
    const doc = await this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const result = await client.questionnaires.get(questionnaireId)
      return questionnaireToJsonApiDoc(unwrapSdk(`GET questionnaires/${questionnaireId}`, result))
    })
    this.questionnaireCache.set(questionnaireId, doc)
    return doc
  }

  // The save path mirrors the production web component: answers are written
  // with `PATCH /use_cases/{id}`, which upserts questionnaire evidence
  // (unlike the private evidence POST, which creates duplicates on re-submit).
  async patchUseCase(useCaseId: string, payload: SavePayload): Promise<Dict> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const result = await client.useCases.update(useCaseId, {
        questions: payload.questions.map((q) => ({
          questionnaireId: q.questionnaire_id,
          sectionId: q.section_id,
          questionId: q.question_id,
          value: q.value,
        })),
        customFields: payload.custom_fields?.map((cf) => ({
          customFieldId: cf.custom_field_id,
          value: cf.value as string | number | boolean | string[] | null,
        })),
        user: payload.user,
      })
      return rec(unwrapSdk(`PATCH use_cases/${useCaseId}`, result))
    })
  }

  // Use-case creation for the intake/create forms. Same integration-service
  // resource the save path uses, so answers are upserted by the same
  // mechanism rather than posted as standalone evidence.
  async createUseCase(payload: UseCaseSubmission & { user: string; source?: string; questionnaireIds: string[] }): Promise<{ id: string }> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const result = await client.useCases.create({
        name: payload.name,
        description: payload.description,
        questionnaireIds: payload.questionnaireIds,
        user: payload.user,
        source: payload.source,
        questions: payload.questions,
        customFields: payload.customFields.map((cf) => ({
          customFieldId: cf.customFieldId,
          value: cf.value as string | number | boolean | string[] | null,
        })),
      })
      return unwrapSdk('POST use_cases', result)
    })
  }

  // One page of the tenant's use cases. Cursor-paginated
  // (`nextCursor`/`hasMore`); `totalCount` is populated.
  async listUseCasesPage(
    pageLimit: number,
    pageAfter?: string,
  ): Promise<{ ids: string[]; nextCursor: string | null; totalCount: number | null }> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const page = unwrapSdk('GET use_cases', await client.useCases.list({ pageLimit, pageAfter }))
      return {
        ids: page.items.map((useCase) => useCase.id),
        nextCursor: page.pagination.hasMore ? (page.pagination.nextCursor ?? null) : null,
        totalCount: page.pagination.totalCount ?? null,
      }
    })
  }

  // Use-case update for the edit form. Distinct from patchUseCase, which
  // takes the transparency card's own SavePayload shape.
  async updateUseCaseFromForm(
    useCaseId: string,
    payload: UseCaseSubmission & { user: string },
  ): Promise<{ id: string }> {
    return this.withRetry(async (tokens) => {
      const client = this.sdkClient(tokens)
      const result = await client.useCases.update(useCaseId, {
        name: payload.name,
        description: payload.description,
        user: payload.user,
        questions: payload.questions,
        customFields: payload.customFields.map((cf) => ({
          customFieldId: cf.customFieldId,
          value: cf.value as string | number | boolean | string[] | null,
        })),
      })
      return unwrapSdk(`PATCH use_cases/${useCaseId}`, result) as { id: string }
    })
  }
}

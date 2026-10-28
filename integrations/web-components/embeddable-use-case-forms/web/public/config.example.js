// Host-page configuration for the Credo AI web components dev pages.
// Copy this file to config.js (gitignored) and fill in real values.
//
// Loaded via a plain <script> tag before the element bundle (see the pages in
// registry/). Nothing here is read by the components themselves: each host
// page reads these values and sets them as ATTRIBUTES on its element. That is
// the real embedding contract; this file is just how the dev pages supply it
// without a rebuild.
//
// The API key is never one of these values. It stays in the recipe's .env, and
// the host pages point the elements at the token service instead.
window.credoConfig = {
  // ---- Shared by every component -----------------------------------------

  tenant: '',

  // Credo AI API. In production both APIs are the same host; set them
  // independently only if yours are served from different hosts or ports.
  apiUrl: 'https://api.credo.ai',
  integrationServiceApiUrl: 'https://api.credo.ai',

  // Where the elements fetch a short-lived token. server/python/ serves it; a
  // production host substitutes its own authenticated endpoint, or drops this
  // and sets the integration-token attribute directly.
  tokenEndpoint: 'http://localhost:8000/api/token',

  // ---- Shared by the two forms -------------------------------------------

  // Attributed as the author of anything the forms write.
  user: '',

  // Optional. When set, the forms link to the created/edited use case in the
  // Credo AI app. Omit to keep users out of the tenant UI entirely.
  appUrl: '',

  // Hide the tenant custom-fields section from the form.
  hideCustomFields: false,

  // Render the form read-only (no Submit/Update button).
  allowSubmit: true,

  // ---- credo-ai-use-case-intake-form -------------------------------------

  // Questionnaire the form submits against. Required.
  questionnaireId: '',

  // Lock the questionnaire dropdown to questionnaireId (true) or let the
  // submitter pick from the tenant's active questionnaires (false).
  disableQuestionnaireSelection: true,

  // Free-text provenance tag recorded on created use cases.
  //
  // Leave this empty. The integration service currently rejects (422) every
  // non-empty value, so setting it makes Submit fail.
  source: '',

  // ---- credo-ai-use-case-edit-form and credo-ai-use-case-preview ---------

  // The use case to edit or preview. Required.
  useCaseId: '',

  // ---- credo-ai-use-case-preview -----------------------------------------
  // Read-only preview of useCaseId. Questionnaire ids match on the base id
  // ('TCV2' matches 'TCV2+25').

  // JSON array of questionnaire ids shown first, in this order, e.g.
  // '["TCV2","AGV1"]'. Unlisted ones follow in attachment order.
  previewQuestionnaireOrder: '',

  // JSON array of questionnaire ids to show. Empty/unset -> every
  // questionnaire attached to the use case.
  previewAllowedQuestionnaires: '',

  // JSON array of tenant custom-field names to show, in this order.
  // Unset -> every custom field with a value; '[]' -> none.
  previewCustomFields: undefined,

  // JSON array of use-case property names shown as metadata rows. Only 'name'
  // and 'description' resolve today.
  useCaseFields: '["name","description"]',

  // Sanitized HTML shown in the preview footer.
  previewFooterText: 'Powered by Credo AI',

  // 'true' hides the use case title and description (for hosts whose page
  // already shows them). Unset or 'false' keeps them.
  hideHeaderText: '',
}

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { runAuthorization, runTokenState } from "./pi_run_token.ts";

const planningConfidence = Type.Union([
  Type.Literal("low"),
  Type.Literal("medium"),
  Type.Literal("high"),
]);

const operationEffect = Type.Union([
  Type.Literal("read"),
  Type.Literal("validate"),
  Type.Literal("mutate"),
  Type.Literal("external_mutate"),
  Type.Literal("unknown"),
]);

const impactDisposition = Type.Union([
  Type.Literal("modify"),
  Type.Literal("verify"),
  Type.Literal("not_impacted"),
]);

const causalStatus = Type.Union([
  Type.Literal("confirmed"),
  Type.Literal("supported"),
  Type.Literal("tentative"),
]);

const requirementPlanCoverage = Type.Object(
  {
    requirement_id: Type.String(),
    plan_item_ids: Type.Optional(Type.Array(Type.String())),
    validation_ids: Type.Optional(Type.Array(Type.String())),
  },
  { additionalProperties: false },
);

const planImpactDisposition = Type.Object(
  {
    candidate_id: Type.String(),
    disposition: impactDisposition,
    reason: Type.Optional(Type.String()),
    evidence_ids: Type.Optional(Type.Array(Type.String())),
    waiver_proof_ids: Type.Optional(Type.Array(Type.String())),
    invariant: Type.Optional(Type.Union([Type.String(), Type.Null()])),
  },
  { additionalProperties: false },
);

const planItem = Type.Object(
  {
    id: Type.String(),
    intent: Type.String(),
    paths: Type.Optional(Type.Array(Type.String())),
    requirement_ids: Type.Optional(Type.Array(Type.String())),
    candidate_ids: Type.Optional(Type.Array(Type.String())),
    validation_ids: Type.Optional(Type.Array(Type.String())),
    allowed_effects: Type.Optional(Type.Array(operationEffect)),
    command_hints: Type.Optional(Type.Array(Type.String())),
  },
  { additionalProperties: false },
);

const planValidationIntent = Type.Object(
  {
    id: Type.String(),
    kind: Type.String(),
    requirement_ids: Type.Optional(Type.Array(Type.String())),
    invariant: Type.Optional(Type.Union([Type.String(), Type.Null()])),
    command_hint: Type.Optional(Type.Union([Type.String(), Type.Null()])),
  },
  { additionalProperties: false },
);

const causalHypothesis = Type.Object(
  {
    hypothesis: Type.String(),
    evidence_ids: Type.Optional(Type.Array(Type.String())),
    confidence: Type.Optional(planningConfidence),
    competing_hypotheses: Type.Optional(Type.Array(Type.String())),
    verification_method: Type.Optional(Type.String()),
    status: Type.Optional(causalStatus),
  },
  { additionalProperties: false },
);

const implementationPlanSubmission = Type.Object(
  {
    previous_plan_revision_id: Type.Optional(Type.Union([Type.String(), Type.Null()])),
    planning_lenses: Type.Optional(Type.Array(Type.String())),
    requirement_coverage: Type.Optional(Type.Array(requirementPlanCoverage)),
    impacts: Type.Optional(Type.Array(planImpactDisposition)),
    changes: Type.Optional(Type.Array(planItem)),
    validations: Type.Optional(Type.Array(planValidationIntent)),
    assumptions: Type.Optional(Type.Array(Type.String())),
    blockers: Type.Optional(Type.Array(Type.String())),
    causal_hypotheses: Type.Optional(Type.Array(causalHypothesis)),
  },
  { additionalProperties: false },
);

function canonicalInput(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalInput).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object).sort().map(key => `${JSON.stringify(key)}:${canonicalInput(object[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function agentVisiblePayload(value: any): any {
  if (Array.isArray(value)) return value.map(agentVisiblePayload);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value)
      .filter(([key]) => key !== "approval_id")
      .map(([key, item]) => [key, agentVisiblePayload(item)]));
  }
  return value;
}

export default function (pi: ExtensionAPI) {
  const runId = process.env.OMNIX_AGENT_RUN_ID || "";
  const baseUrl = process.env.OMNIX_AGENT_BROKER_URL || "http://127.0.0.1:8000/api/agent-runs";
  const allowed = new Set<string>(JSON.parse(process.env.OMNIX_AGENT_EXTERNAL_CAPABILITIES || "[]"));
  const localAllowed = new Set<string>(JSON.parse(process.env.OMNIX_AGENT_LOCAL_CAPABILITIES || "[]"));
  let usedManagedWorkspacePreview = false;
  // Approval identities stay in trusted extension state, outside the model's
  // parameters and results. The server binds each identity to this exact input.
  const pendingApprovals = new Map<string, string>();
  // Take the run token out of the environment before any tool runs (WP-4.6).
  runTokenState();
  if (!runId) return;

  const planningInspection = async (signal: AbortSignal): Promise<any | null> => {
    try {
      const response = await fetch(`${baseUrl}/${encodeURIComponent(runId)}/planning/inspect`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(baseUrl, runId) },
        body: JSON.stringify({ queries: [], paths: [] }),
        signal,
      });
      if (!response.ok) return null;
      return await response.json();
    } catch {
      // Server-side planning gates remain authoritative. Preflight is a model-
      // guidance optimization and must not replace or weaken those gates.
      return null;
    }
  };

  const planningReviewerTransportBlock = (inspection: any): any | null => {
    const state = inspection?.planning_state;
    if (String(state?.status || "") !== "blocked") return null;
    const taskRevisionId = String(inspection?.task_revision_id || "").trim();
    const stateRevisionId = String(state?.task_revision_id || "").trim();
    if (taskRevisionId && stateRevisionId && taskRevisionId !== stateRevisionId) return null;
    return {
      approved: false,
      blocked: true,
      terminal: true,
      reviewer_transport_blocked: true,
      reason: "plan_semantic_review_transport_exhausted",
      task_revision_id: taskRevisionId || stateRevisionId || null,
      planning_state: state,
      instruction: "Independent semantic plan-review transport retries are exhausted for this task revision. Do not resubmit or amend the same plan and do not broaden repository inspection. Surface the blocked reviewer state; a later user retry or new authoritative task revision may start a fresh review cycle.",
    };
  };

  const planningReferenceMismatch = (plan: any, inspection: any): any | null => {
    const validRequirementIds = new Set<string>(
      (Array.isArray(inspection?.requirements) ? inspection.requirements : [])
        .map((item: any) => String(item?.id || "").trim())
        .filter(Boolean),
    );
    const validCandidateIds = new Set<string>(
      (Array.isArray(inspection?.impact_candidates) ? inspection.impact_candidates : [])
        .map((item: any) => String(item?.candidate_id || "").trim())
        .filter(Boolean),
    );
    const referencedRequirementIds = new Set<string>();
    const referencedCandidateIds = new Set<string>();
    const collect = (values: unknown, target: Set<string>): void => {
      if (!Array.isArray(values)) return;
      values.forEach((value) => {
        const normalized = String(value || "").trim();
        if (normalized) target.add(normalized);
      });
    };

    for (const coverage of Array.isArray(plan?.requirement_coverage) ? plan.requirement_coverage : []) {
      const id = String(coverage?.requirement_id || "").trim();
      if (id) referencedRequirementIds.add(id);
    }
    for (const impact of Array.isArray(plan?.impacts) ? plan.impacts : []) {
      const id = String(impact?.candidate_id || "").trim();
      if (id) referencedCandidateIds.add(id);
    }
    for (const change of Array.isArray(plan?.changes) ? plan.changes : []) {
      collect(change?.requirement_ids, referencedRequirementIds);
      collect(change?.candidate_ids, referencedCandidateIds);
    }
    for (const validation of Array.isArray(plan?.validations) ? plan.validations : []) {
      collect(validation?.requirement_ids, referencedRequirementIds);
    }

    const invalidRequirementIds = [...referencedRequirementIds].filter((id) => !validRequirementIds.has(id));
    const invalidCandidateIds = [...referencedCandidateIds].filter((id) => !validCandidateIds.has(id));
    if (!invalidRequirementIds.length && !invalidCandidateIds.length) return null;

    return {
      approved: false,
      preflight_required: true,
      reason: "planning_contract_reference_mismatch",
      task_revision_id: inspection?.task_revision_id ?? null,
      invalid_requirement_ids: invalidRequirementIds,
      valid_requirement_ids: [...validRequirementIds],
      invalid_candidate_ids: invalidCandidateIds,
      valid_candidate_ids: [...validCandidateIds],
      instruction: validCandidateIds.size
        ? "Resubmit immediately using only the exact requirement_id and candidate_id values listed by Omnix. Do not invent aliases, path names, or plan-item IDs as authority references, and do not perform more repository inspection merely to repair this contract mismatch."
        : "Resubmit immediately using only the exact requirement_id values listed by Omnix. impact_candidates is empty, so omit impacts and every candidate_ids field; never invent aliases, path names, C1/C2-style labels, or plan-item IDs as candidate IDs. Do not perform more repository inspection merely to repair this contract mismatch.",
    };
  };

  pi.registerTool({
    name: "omnix_plan",
    label: "Omnix Plan",
    description: "Persist or inspect the coding agent's working plan. Independent semantic review is reserved for high-risk plans such as migrations, dependency or generated-contract changes, destructive operations, security-sensitive work, trading logic, and unusually broad changes. Ordinary in-scope edits remain advisory; Omnix requires hard plan authority only for consequential mutations.",
    promptSnippet: "High-risk working plans receive independent objective-fidelity review; ordinary coding remains Pi-native",
    promptGuidelines: [
      "Use your normal Pi planning/replanning loop for ordinary coding. A working plan is useful for audit/recovery/review context but is not permission for normal in-scope source/test edits.",
      "The plan parameter schema is the authoritative submission contract. Use only those fields and enum values; do not invent generic planning fields such as summary or steps.",
      "Requirement and impact references are authority identities, not labels you may invent. Use only exact requirement IDs and candidate IDs returned by omnix_plan inspect. If impact_candidates is empty, omit impacts and every candidate_ids field; never substitute paths, C1/C2-style aliases, or plan-item IDs.",
      "The broker preflights submit/amend against a fresh planning inspection. If it returns planning_contract_reference_mismatch, resubmit immediately from the listed valid IDs; do not spend repository read/search calls trying to repair an identity mismatch.",
      "When submit/amend returns semantic_review findings, treat that response as an independent fresh-session critique of your proposed plan, not as repository authority. Re-read the authoritative user task and evaluate each blocking finding before resubmitting.",
      "If planning_state is blocked or reviewer_transport_blocked is true, reviewer infrastructure retries are exhausted for this task revision. Do not resubmit/amend the same plan and do not broaden inspection; surface the blocked state and stop this execution attempt until a later user retry or new authoritative task revision.",
      "A blocking objective-fidelity finding means the plan may solve the wrong problem or reverse the requested before-to-after behavior. Correct the plan rather than continuing broad repository inspection merely to defend the previous interpretation.",
      "Consensus means no remaining blocking semantic-review findings. Major/minor/suggestion findings are advisory and do not require agreement. If Omnix reports consensus exhaustion, surface the unresolved disagreement or a concise clarification need instead of looping on more inspection.",
      "Once submit/amend returns approved:true, transition from discovery to execution. Additional reads/searches should answer a specific unresolved implementation question; repeated broad inspection that does not change the target, plan, or validation strategy is not progress and may be blocked by Omnix.",
      "Do not stop to amend the plan merely because you discover another ordinary in-scope caller or test while implementing.",
      "Use action=inspect to obtain exact planning authority identities or when a deterministic repository search would help your reasoning/audit evidence; Omnix does not infer semantic task lenses for you.",
      "If Omnix blocks a consequential mutation because hard planning authority is required, submit or amend a narrow plan covering that exact path/command, then retry it.",
      "Use action=check for diagnostics when useful; advisory conformance is not completion authority.",
      "Plan approval never grants capabilities, external authority, or user approval. Workspace, capability, approval, budget, and final acceptance policies remain independent.",
    ],
    parameters: Type.Object({
      action: Type.Union([
        Type.Literal("inspect"),
        Type.Literal("submit"),
        Type.Literal("amend"),
        Type.Literal("check"),
      ]),
      queries: Type.Optional(Type.Array(Type.String())),
      paths: Type.Optional(Type.Array(Type.String())),
      plan: Type.Optional(implementationPlanSubmission),
    }),
    async execute(_toolCallId, params, signal) {
      const action = String(params.action || "");
      if (action === "submit" || action === "amend") {
        const inspection = await planningInspection(signal);
        if (inspection) {
          const transportBlock = planningReviewerTransportBlock(inspection);
          if (transportBlock) {
            return {
              content: [{ type: "text", text: JSON.stringify(transportBlock) }],
              details: transportBlock,
            };
          }
          const mismatch = planningReferenceMismatch(params.plan || {}, inspection);
          if (mismatch) {
            return {
              content: [{ type: "text", text: JSON.stringify(mismatch) }],
              details: mismatch,
            };
          }
        }
      }

      const response = await fetch(`${baseUrl}/${encodeURIComponent(runId)}/planning/${encodeURIComponent(action)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(baseUrl, runId) },
        body: JSON.stringify(
          action === "inspect"
            ? { queries: params.queries || [], paths: params.paths || [] }
            : action === "check"
              ? {}
              : { plan: params.plan || {} },
        ),
        signal,
      });
      let payload: any = {};
      try {
        payload = await response.json();
      } catch {
        payload = { detail: `HTTP ${response.status}` };
      }
      payload = agentVisiblePayload(payload);
      if (!response.ok) {
        return {
          content: [{ type: "text", text: `Omnix planning error: ${JSON.stringify(payload)}` }],
          details: { error: true, payload },
        };
      }
      if (String(payload?.semantic_review?.status || "") === "unavailable") {
        payload.reviewer_transport_blocked = true;
        payload.terminal = true;
        payload.next_action = "Independent semantic plan-review transport retries are exhausted for this task revision. Do not resubmit/amend the same plan or broaden inspection; surface the blocked reviewer state and stop this execution attempt until a later user retry or new authoritative task revision.";
      }
      return {
        content: [{ type: "text", text: JSON.stringify(payload) }],
        details: payload,
      };
    },
  });

  if (localAllowed.has("workspace.run_change_set")) {
    pi.registerTool({
      name: "omnix_change_set",
      label: "Omnix Run Change Set",
      description: "Read the canonical complete run-owned change subject for the exact current candidate. This, not shell git diff, is final-diff authority.",
      promptSnippet: "Inspect the authoritative baseline-relative RunChangeSet",
      promptGuidelines: [
        "Use this tool for final-diff inspection. The returned subject is baseline-relative and includes run-added untracked content references.",
        "The exact workspace may contain baseline dirties; treat them as context, not run-owned subject paths.",
      ],
      parameters: Type.Object({}),
      async execute(_toolCallId, _params, signal) {
        const response = await fetch(`${baseUrl}/${encodeURIComponent(runId)}/run-change-set`, {
          signal,
          headers: { "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(baseUrl, runId) },
        });
        let payload: any = {};
        try { payload = await response.json(); } catch { payload = { detail: `HTTP ${response.status}` }; }
        payload = agentVisiblePayload(payload);
        if (!response.ok) {
          return { content: [{ type: "text", text: `Omnix change-set error: ${JSON.stringify(payload)}` }], details: { error: true, payload } };
        }
        return { content: [{ type: "text", text: JSON.stringify(payload) }], details: payload };
      },
    });
  }

  if (allowed.size === 0) return;

  pi.registerTool({
    name: "omnix_capability",
    label: "Omnix Capability",
    description: "Invoke one canonical external capability issued by Omnix. Mutations may require approval.",
    promptSnippet: "Use governed Omnix capabilities for external systems",
    promptGuidelines: [
      "Use omnix_capability only with capability IDs issued in the task authority.",
      "If omnix_capability reports approval is required, do not claim the action happened; wait for user approval in Omnix and retry the same capability and input.",
      "For local web/UI validation, call browser.open with input { workspace_preview: true, path: '/<route>' } instead of starting npm/vite through the shell. Omnix owns the exact-worktree preview lifecycle and automatically cleans it up after a passing deterministic browser assertion.",
      "After a managed workspace preview has been used, do not request browser.close for cleanup; Omnix owns cleanup and suppresses redundant close calls so they cannot create approval waits.",
    ],
    parameters: Type.Object({
      capability_id: Type.String(),
      input: Type.Optional(Type.Record(Type.String(), Type.Any())),
    }),
    async execute(toolCallId, params, signal) {
      if (!allowed.has(params.capability_id)) {
        return { content: [{ type: "text", text: "Blocked: capability is outside the issued Omnix RunSpec." }], details: { blocked: true } };
      }
      if (params.capability_id === "browser.close" && usedManagedWorkspacePreview) {
        return {
          content: [{ type: "text", text: "Managed workspace preview cleanup is Omnix-owned; no explicit browser.close call is required." }],
          details: {
            capability_id: "browser.close",
            executed: true,
            approval_required: false,
            managed_cleanup: true,
          },
        };
      }
      const approvalKey = canonicalInput({ capability_id: params.capability_id, input: params.input || {} });
      const response = await fetch(`${baseUrl}/${encodeURIComponent(runId)}/capabilities/${params.capability_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(baseUrl, runId) },
        body: JSON.stringify({ input: params.input || {}, approval_id: pendingApprovals.get(approvalKey), proposal_id: toolCallId }),
        signal,
      });
      const payload = await response.json();
      const visiblePayload = agentVisiblePayload(payload);
      if (!response.ok) return { content: [{ type: "text", text: `Omnix broker error: ${JSON.stringify(visiblePayload)}` }], details: { error: true, payload: visiblePayload } };
      if (payload.approval_required) {
        if (typeof payload.approval_id === "string") pendingApprovals.set(approvalKey, payload.approval_id);
        return { content: [{ type: "text", text: `Approval required before ${params.capability_id}. Ask the user to approve it in Omnix, then retry the same capability and input.` }], details: visiblePayload };
      }
      if (
        params.capability_id === "browser.open"
        && params.input?.workspace_preview === true
        && payload.executed === true
      ) {
        usedManagedWorkspacePreview = true;
      }
      return { content: [{ type: "text", text: JSON.stringify(visiblePayload.result) }], details: visiblePayload };
    },
  });
}

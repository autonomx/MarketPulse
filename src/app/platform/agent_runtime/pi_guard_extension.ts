import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import fs from "node:fs";
import path from "node:path";
import { runAuthorization, runTokenState } from "./pi_run_token.ts";

const workspace = path.resolve(process.env.OMNIX_AGENT_WORKSPACE || process.cwd());
const realWorkspace = fs.realpathSync(workspace);
const runId = process.env.OMNIX_AGENT_RUN_ID || "";
const brokerUrl = process.env.OMNIX_AGENT_BROKER_URL || "http://127.0.0.1:8000/api/agent-runs";
// Take the run token out of the environment before any tool runs (WP-4.6).
runTokenState();
const approvalPolicy = process.env.OMNIX_AGENT_APPROVAL_POLICY || "ask_sensitive";
// Set only inside the Docker sandbox (WP-4.7). Safe validation commands are
// approved automatically there; outside it every command asks.
const sandboxed = process.env.OMNIX_AGENT_SANDBOXED === "1";

function stringList(name: string, fallback: string[]): string[] {
  try {
    const parsed = JSON.parse(process.env[name] || "");
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string") : fallback;
  } catch {
    return fallback;
  }
}

const allowedPaths = stringList("OMNIX_AGENT_ALLOWED_PATHS", ["**"]);
const forbiddenPaths = stringList("OMNIX_AGENT_FORBIDDEN_PATHS", []);
const localCapabilities = new Set(stringList("OMNIX_AGENT_LOCAL_CAPABILITIES", []));

type IssuedPathRoot = {
  rootId: string;
  rootPath: string;
  realRoot: string;
  access: "read_only" | "read_write";
};

function issuedPathRoots(): IssuedPathRoot[] {
  const roots: IssuedPathRoot[] = [{
    rootId: "workspace",
    rootPath: workspace,
    realRoot: realWorkspace,
    access: localCapabilities.has("workspace.edit") || localCapabilities.has("workspace.write")
      ? "read_write"
      : "read_only",
  }];
  let configured: unknown = [];
  try {
    configured = JSON.parse(process.env.OMNIX_AGENT_PATH_ROOTS || "[]");
  } catch {
    configured = [];
  }
  if (!Array.isArray(configured)) return roots;
  const seenIds = new Set(["workspace"]);
  const seenPaths = new Set([path.normalize(realWorkspace).toLowerCase()]);
  for (const value of configured) {
    if (!value || typeof value !== "object") continue;
    const item = value as Record<string, unknown>;
    const rootId = typeof item.root_id === "string" ? item.root_id.trim().toLowerCase() : "";
    const configuredPath = typeof item.path === "string" ? item.path.trim() : "";
    if (!/^[a-z0-9_.-]{1,64}$/.test(rootId) || rootId === "workspace" || !path.isAbsolute(configuredPath)) continue;
    let realRoot: string;
    try {
      if (!fs.statSync(configuredPath).isDirectory()) continue;
      realRoot = fs.realpathSync(configuredPath);
    } catch {
      continue;
    }
    const normalized = path.normalize(realRoot).toLowerCase();
    if (seenIds.has(rootId) || seenPaths.has(normalized)) continue;
    roots.push({
      rootId,
      rootPath: path.resolve(configuredPath),
      realRoot,
      // Additional repositories are evidence-only even if malformed input
      // claims otherwise. The active workspace remains the sole writable root.
      access: "read_only",
    });
    seenIds.add(rootId);
    seenPaths.add(normalized);
  }
  return roots;
}

const pathRoots = issuedPathRoots();
const pathRootsById = new Map(pathRoots.map((root) => [root.rootId, root]));

type ToolPathResolution = {
  root: IssuedPathRoot;
  rootRelative: string;
  executionPath: string;
  qualified: boolean;
};

function realPathWithinRoot(root: IssuedPathRoot, candidate: string): boolean {
  let probe = candidate;
  while (true) {
    try {
      fs.lstatSync(probe);
      break;
    } catch {
      const parent = path.dirname(probe);
      if (parent === probe) return false;
      probe = parent;
    }
  }
  let realProbe: string;
  try {
    realProbe = fs.realpathSync(probe);
  } catch {
    return false;
  }
  const suffix = path.relative(probe, candidate);
  const reconstructed = path.resolve(realProbe, suffix);
  const relative = path.relative(root.realRoot, reconstructed);
  return !(
    relative === ".."
    || relative.startsWith(".." + path.sep)
    || path.isAbsolute(relative)
  );
}

function resolveIssuedToolPath(value: unknown, writable: boolean): ToolPathResolution | null | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  const raw = value.trim();
  if (!raw.startsWith("@")) {
    if (!pathAllowed(raw)) return null;
    const relative = relativeWorkspacePath(raw);
    if (relative === null) return null;
    return {
      root: pathRootsById.get("workspace")!,
      rootRelative: relative,
      executionPath: raw,
      qualified: false,
    };
  }
  const match = raw.match(/^@([A-Za-z0-9_.-]{1,64})(?:[\\/](.*))?$/);
  if (!match) return null;
  const root = pathRootsById.get(match[1].toLowerCase());
  if (!root || (writable && root.access !== "read_write")) return null;
  const suffix = String(match[2] || ".").replace(/[\\/]+/g, path.sep);
  const candidate = path.resolve(root.rootPath, suffix);
  const relative = path.relative(root.rootPath, candidate);
  if (relative === ".." || relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) return null;
  const rootRelative = (relative || ".").split(path.sep).join("/");
  if (!realPathWithinRoot(root, candidate)) return null;
  if (root.rootId === "workspace") {
    if (matches(forbiddenPaths, rootRelative)) return null;
    if (allowedPaths.length > 0 && !matches(allowedPaths, rootRelative)) return null;
  }
  return {
    root,
    rootRelative,
    executionPath: root.rootId === "workspace" ? rootRelative : candidate,
    qualified: true,
  };
}

type SearchPathContext = {
  root: IssuedPathRoot;
  searchRoot: string;
};

const searchPathContexts = new Map<string, SearchPathContext>();

function displayRootPath(root: IssuedPathRoot, rootRelative: string): string {
  const normalized = rootRelative.split(path.sep).join("/").replace(/^\.\//, "") || ".";
  return root.rootId === "workspace" ? normalized : `@${root.rootId}/${normalized}`;
}

function normalizeGrepLine(line: string, context: SearchPathContext): string {
  const match = line.match(/^(.+?)([:\-])(\d+)([:\-])(.*)$/);
  if (!match) return line;
  const reported = match[1].replace(/\\/g, "/");
  let rootRelative: string;
  if (path.isAbsolute(reported)) {
    const relative = path.relative(context.root.rootPath, reported);
    if (relative === ".." || relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) return line;
    rootRelative = (relative || ".").split(path.sep).join("/");
  } else {
    const base = context.searchRoot === "." ? "" : context.searchRoot.replace(/\/$/, "") + "/";
    rootRelative = reported === context.searchRoot || reported.startsWith(base)
      ? reported
      : base + reported;
  }
  return displayRootPath(context.root, rootRelative) + match[2] + match[3] + match[4] + match[5];
}

function relativeWorkspacePath(value: string): string | null {
  const cleaned = value.startsWith("@") ? value.slice(1) : value;
  const resolved = path.resolve(workspace, cleaned);
  const relative = path.relative(workspace, resolved);
  if (relative === ".." || relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) return null;
  return (relative || ".").split(path.sep).join("/");
}

function globRegex(pattern: string): RegExp {
  let value = pattern.split("\\").join("/");
  value = value.replace(/[.+^$(){}|[\]\\]/g, "\\$&");
  value = value.replace(/\*\*/g, "__DOUBLE_STAR__");
  value = value.replace(/\*/g, "[^/]*");
  value = value.replace(/\?/g, "[^/]");
  value = value.replace(/__DOUBLE_STAR__/g, ".*");
  return new RegExp("^" + value + "$");
}

function matches(patterns: string[], relative: string): boolean {
  return patterns.some((pattern) => {
    if (pattern === "**") return true;
    const normalized = pattern.split("\\").join("/");
    if (normalized.endsWith("/**") && relative === normalized.slice(0, -3)) {
      return true;
    }
    return globRegex(normalized).test(relative);
  });
}

function realPathWithinWorkspace(value: string): boolean {
  const cleaned = value.startsWith("@") ? value.slice(1) : value;
  const candidate = path.resolve(workspace, cleaned);
  let probe = candidate;
  while (true) {
    try {
      fs.lstatSync(probe);
      break;
    } catch {
      const parent = path.dirname(probe);
      if (parent === probe) return false;
      probe = parent;
    }
  }
  let realProbe: string;
  try {
    realProbe = fs.realpathSync(probe);
  } catch {
    // A dangling symlink (or other unresolvable existing path) is not a safe
    // parent for a read/write target.
    return false;
  }
  const suffix = path.relative(probe, candidate);
  const reconstructed = path.resolve(realProbe, suffix);
  const relative = path.relative(realWorkspace, reconstructed);
  return !(
    relative === ".."
    || relative.startsWith(".." + path.sep)
    || path.isAbsolute(relative)
  );
}

function pathAllowed(value: unknown): boolean {
  if (typeof value !== "string" || !value.trim()) return true;
  const relative = relativeWorkspacePath(value);
  if (relative === null || !realPathWithinWorkspace(value)) return false;
  if (matches(forbiddenPaths, relative)) return false;
  return allowedPaths.length === 0 || matches(allowedPaths, relative);
}

const safeCommandPrefixes = [
  "git status", "git diff", "git log", "git show", "git grep",
  "python -m pytest", "python -m py_compile", "pytest", "ruff",
  "npm test", "npm run test", "npm run build", "npm run typecheck", "npm run lint",
  "npm ci --ignore-scripts",
  "npx vitest", "npx tsc",
];

const testCommandPrefixes = [
  "python -m pytest", "pytest", "npm test", "npm run test", "npx vitest",
];

const gitStatusCommandPrefixes = ["git status"];
const gitDiffCommandPrefixes = ["git diff"];
// npm script names commonly use suffixes such as test:e2e, test:unit, build:ci,
// and lint:strict. These remain validation when their base script family is
// validation; arbitrary custom scripts still fall through to exact command
// authority instead of being treated as safe by prefix coincidence.
const npmTestCommand = /^npm(?:\.cmd)?(?:\s+--prefix\s+\S+)*\s+(?:test|run\s+test(?:[-_:][A-Za-z0-9_.-]+)?)(?:\s|$)/i;
const npmSafeValidationCommand = /^npm(?:\.cmd)?(?:\s+--prefix\s+\S+)*\s+(?:test|run\s+(?:test|build|typecheck|lint)(?:[-_:][A-Za-z0-9_.-]+)?)(?:\s|$)/i;
const directPlaywrightTestCommand = /(?:^|\s)(?:npx\s+)?playwright(?:\.cmd)?\s+test(?:\s|$)/i;
const npmPlaywrightTestCommand = /^npm(?:\.cmd)?(?:\s+--prefix\s+\S+)*\s+run\s+test(?:[-_:](?:e2e|ui|playwright))(?:[-_:][A-Za-z0-9_.-]+)*(?:\s|$)/i;
const playwrightFileLineSelector = /(?:^|\s)(?:"[^"]+|'[^']+'|\S+)\.(?:spec|test)\.[cm]?[jt]sx?:\d+(?=\s|$)/gi;

function issuedCommandPrefixes(): string[] {
  if (localCapabilities.has("workspace.command")) return safeCommandPrefixes;
  const prefixes = new Set<string>();
  if (localCapabilities.has("workspace.test")) {
    for (const prefix of testCommandPrefixes) prefixes.add(prefix);
  }
  if (localCapabilities.has("workspace.git_status")) {
    for (const prefix of gitStatusCommandPrefixes) prefixes.add(prefix);
  }
  if (localCapabilities.has("workspace.git_diff")) {
    for (const prefix of gitDiffCommandPrefixes) prefixes.add(prefix);
  }
  return [...prefixes];
}

const forbiddenShellSyntax = /[\r\n;&|><`]/;
const environmentExpansion = /(?:\$\{|\$[A-Za-z_]|%[A-Za-z_][A-Za-z0-9_]*%|~[\\/])/;
const managedPreviewShellCommand = /(?:\bnpm(?:\.cmd)?\b[\s\S]{0,320}\brun\b[\s\S]{0,120}\b(?:dev|preview)\b|\b(?:npx\s+)?vite(?:\.cmd)?\b)/i;
const inlinePythonCommand = /^(?:python|python3)(?:\.exe)?\s+-c(?:\s|$)/i;

const investigationTools = new Set(["read", "grep", "find", "ls"]);
const readOnlyGitCommand = /^(?:git\s+(?:status|diff|log|show|grep))(?:\s|$)/i;
const postPlanInvestigationLimit = (() => {
  const configured = Number.parseInt(process.env.OMNIX_AGENT_POST_PLAN_INVESTIGATION_LIMIT || "10", 10);
  return Number.isFinite(configured) && configured >= 3 ? configured : 10;
})();
let progressPlanRevisionId: string | null = null;
let postPlanInvestigationCalls = 0;
let postPlanBlockedAttempts = 0;

function normalizeCommand(command: unknown): string {
  return typeof command === "string"
    ? command.trim().toLowerCase().replace(/^(npx|npm|python)\.cmd(?=\s|$)/, "$1")
    : "";
}

function isValidationCommand(command: unknown): boolean {
  const normalized = normalizeCommand(command);
  if (!normalized) return false;
  if (npmSafeValidationCommand.test(normalized)) return true;
  return [
    "python -m pytest",
    "python -m py_compile",
    "pytest",
    "ruff",
    "npx vitest",
    "npx tsc",
  ].some((prefix) => normalized === prefix || normalized.startsWith(prefix + " "));
}

function isProgressAction(toolName: string, input: Record<string, unknown>): boolean {
  if (["edit", "write", "omnix_change_set", "omnix_capability"].includes(toolName)) return true;
  return (toolName === "bash" || toolName === "powershell") && isValidationCommand(input.command);
}

function isInvestigationAction(toolName: string, input: Record<string, unknown>): boolean {
  if (investigationTools.has(toolName)) return true;
  if (toolName !== "bash" && toolName !== "powershell") return false;
  return readOnlyGitCommand.test(normalizeCommand(input.command));
}

function resetPostPlanProgress(): void {
  postPlanInvestigationCalls = 0;
  postPlanBlockedAttempts = 0;
}

async function currentApprovedPlanRevisionId(): Promise<string | null | undefined> {
  if (!runId) return null;
  try {
    const response = await fetch(`${brokerUrl}/${encodeURIComponent(runId)}/planning/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(brokerUrl, runId) },
    });
    let payload: any = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (response.status === 409 && payload?.detail === "agent_planning_not_applicable") return null;
    if (!response.ok) return undefined;
    const planRevisionId = typeof payload?.plan_revision_id === "string" ? payload.plan_revision_id.trim() : "";
    return payload?.passed === true && planRevisionId ? planRevisionId : null;
  } catch {
    // The progress guard is an efficiency policy, not correctness authority.
    // A temporary planning/check outage must not strand an otherwise valid run.
    return undefined;
  }
}

async function postPlanProgressRejection(
  toolName: string,
  input: Record<string, unknown>,
): Promise<string | null> {
  if (isProgressAction(toolName, input)) {
    resetPostPlanProgress();
    return null;
  }
  if (!isInvestigationAction(toolName, input)) return null;

  const approvedPlanRevisionId = await currentApprovedPlanRevisionId();
  if (approvedPlanRevisionId === undefined) return null;
  if (!approvedPlanRevisionId) {
    progressPlanRevisionId = null;
    resetPostPlanProgress();
    return null;
  }
  if (progressPlanRevisionId !== approvedPlanRevisionId) {
    progressPlanRevisionId = approvedPlanRevisionId;
    resetPostPlanProgress();
  }

  postPlanInvestigationCalls += 1;
  if (postPlanInvestigationCalls <= postPlanInvestigationLimit) return null;

  postPlanBlockedAttempts += 1;
  const repeated = postPlanBlockedAttempts > 1
    ? ` This is blocked retry ${postPlanBlockedAttempts}; do not keep retrying read/search tools.`
    : "";
  return (
    `Omnix post-plan progress guard blocked further broad investigation after ${postPlanInvestigationLimit} `
    + `consecutive read/search calls under approved plan ${approvedPlanRevisionId}.${repeated} `
    + "The working plan is approved: transition from discovery to execution. Make the smallest planned edit, "
    + "run focused validation, use a governed browser assertion when applicable, or amend the plan if a specific "
    + "new blocker materially changes the implementation. Additional reads/searches are allowed again after "
    + "meaningful execution progress or an approved plan revision."
  );
}

function commandScopeAllowed(command: string): boolean {
  if (environmentExpansion.test(command)) return false;
  // Python source passed to ``-c`` can contain escaped quotes and backslashes.
  // Those are code characters, not workspace paths. The command is still
  // outside the built-in safe prefixes and therefore goes through the exact
  // broker approval path before execution.
  if (inlinePythonCommand.test(command.trim())) return true;
  const tokens = command.match(/"[^"]*"|\'[^\']*\'|\S+/g) || [];
  for (const rawToken of tokens.slice(1)) {
    let token = rawToken.replace(/^["\']|["\']$/g, "");
    if (!token) continue;
    const equalsIndex = token.indexOf("=");
    if (token.startsWith("-")) {
      if (equalsIndex < 0) continue;
      token = token.slice(equalsIndex + 1);
    } else if (equalsIndex >= 0) {
      token = token.slice(equalsIndex + 1);
    }
    if (!token) continue;
    const normalized = token.replace(/\\/g, "/");
    if (normalized === ".." || normalized.startsWith("../") || normalized.includes("/../")) return false;
    const looksLikePath = path.isAbsolute(token) || token.includes("/") || token.includes("\\") || token.startsWith(".");
    if (looksLikePath && !pathAllowed(token)) return false;
  }
  return true;
}

function commandSafetyRejectionReason(command: unknown): string | null {
  if (typeof command !== "string" || !command.trim()) {
    return "Omnix command policy requires a non-empty command.";
  }
  const normalized = command.trim().toLowerCase();
  if (forbiddenShellSyntax.test(normalized) || normalized.includes("$(")) {
    return "Omnix command policy blocks shell chaining, pipes, redirection, command substitution, and multi-command syntax. Run each allowed command as a separate tool call.";
  }
  if (managedPreviewShellCommand.test(normalized)) {
    return "Omnix owns the local web preview lifecycle. Do not launch npm/vite dev or preview servers through shell commands. For governed UI validation, call browser.open through omnix_capability with input { workspace_preview: true, path: \"/<route>\" }; Omnix will allocate a loopback port and clean it up automatically.";
  }
  if (
    (directPlaywrightTestCommand.test(normalized) || npmPlaywrightTestCommand.test(normalized))
    && (normalized.match(playwrightFileLineSelector) || []).length !== 1
  ) {
    return "Omnix limits UI Playwright validation to one focused test per command. Select exactly one test with a relative spec file and source line (for example tests/e2e/app-shell.spec.ts:306); do not run a whole spec, suite, or grep pattern.";
  }
  if (!commandScopeAllowed(command)) {
    return "Omnix command policy blocked an out-of-scope path or unsafe environment/path expansion. Keep command paths inside the issued workspace.";
  }
  return null;
}

function commandPrefixAllowed(command: string): boolean {
  const normalized = command.trim().toLowerCase().replace(/^(npx|npm|python)\.cmd(?=\s|$)/, "$1");
  // `npm --prefix <dir>` is an option form, not a capability by itself. Only
  // explicit validation subcommands are safe under the corresponding issued
  // capability; dependency-changing commands must fall through to workspace.command
  // approval (or be rejected when only workspace.test was issued).
  if (localCapabilities.has("workspace.test") && npmTestCommand.test(normalized)) return true;
  if (localCapabilities.has("workspace.command") && npmSafeValidationCommand.test(normalized)) return true;
  return issuedCommandPrefixes().some((prefix) => normalized === prefix || normalized.startsWith(prefix + " "));
}

function commandRejectionReason(command: unknown): string | null {
  const safetyRejection = commandSafetyRejectionReason(command);
  if (safetyRejection) return safetyRejection;
  if (typeof command !== "string" || !commandPrefixAllowed(command)) {
    const prefixes = issuedCommandPrefixes();
    return `Omnix command policy does not allow that command prefix. Use one of: ${prefixes.join(", ") || "no shell commands issued"}.`;
  }
  return null;
}

async function authorizePlanningOperation(
  toolName: string,
  input: Record<string, unknown>,
): Promise<string | null> {
  if (!runId) return "Omnix run identity is missing.";
  try {
    const response = await fetch(`${brokerUrl}/${encodeURIComponent(runId)}/planning/authorize`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(brokerUrl, runId) },
      body: JSON.stringify({
        tool_name: toolName,
        input,
        path: typeof input.path === "string" ? input.path : null,
        command: typeof input.command === "string" ? input.command : null,
      }),
    });
    let payload: any = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    // Planning is applicable only to mutating coding runs. Review-only and
    // non-coding profiles retain their existing authority model.
    if (response.status === 409 && payload?.detail === "agent_planning_not_applicable") return null;
    if (!response.ok) {
      const detail = typeof payload?.detail === "string" ? payload.detail : `HTTP ${response.status}`;
      return `Omnix planning authorization unavailable: ${detail}`;
    }
    if (payload?.allowed === true) return null;
    const reasonItems = Array.isArray(payload?.reasons)
      ? payload.reasons.map((value: unknown) => String(value))
      : [];
    const protectedReason = reasonItems.find((value: string) => value.startsWith("preexisting_dirty_path_mutation_forbidden:"));
    if (protectedReason) {
      const protectedPath = protectedReason.slice("preexisting_dirty_path_mutation_forbidden:".length) || "the baseline-dirty path";
      return (
        `Omnix workspace provenance blocked mutation of ${protectedPath} because that path was already dirty when this run began. `
        + "A PlanDelta cannot authorize overwriting pre-existing workspace changes. Preserve that path, use another source/test path, "
        + "or ask the user to resolve the pre-existing change. Do not retry the same edit/write."
      );
    }
    const reasons = reasonItems.length ? reasonItems.join(", ") : String(payload?.reason || "plan not approved");
    return `Omnix hard planning authority blocked this consequential operation: ${reasons}. Record the narrow required path/command with omnix_plan before retrying; ordinary in-scope edits do not require PlanDelta round trips.`;
  } catch (error) {
    return `Omnix planning authorization unavailable: ${String(error)}`;
  }
}

async function authorizeBlockedCommand(command: string, cwd: unknown): Promise<string | null> {
  if (!runId) return "Omnix run identity is missing.";
  try {
    const response = await fetch(`${brokerUrl}/${encodeURIComponent(runId)}/command-authorization`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(brokerUrl, runId) },
      body: JSON.stringify({
        command,
        cwd: typeof cwd === "string" ? cwd : workspace,
        workspace_root: workspace,
      }),
    });
    let payload: any = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (response.ok && payload?.allowed === true) return null;
    if (response.ok && payload?.approval_required === true) {
      return "Omnix approval required for this exact workspace command. Ask the user to approve it in Omnix, then retry the same command.";
    }
    const detail = typeof payload?.detail === "string"
      ? payload.detail
      : typeof payload?.reason === "string" ? payload.reason : `HTTP ${response.status}`;
    return `Omnix command permission denied: ${detail}`;
  } catch (error) {
    return `Omnix command permission service unavailable: ${String(error)}`;
  }
}

async function authorizeWorkspaceTool(toolName: string, input: Record<string, unknown>): Promise<string | null> {
  if (!runId) return "Omnix run identity is missing.";
  try {
    const response = await fetch(`${brokerUrl}/${encodeURIComponent(runId)}/workspace-authorization`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(brokerUrl, runId) },
      body: JSON.stringify({ tool_name: toolName, input, workspace_root: workspace }),
    });
    let payload: any = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (response.ok && payload?.allowed === true) return null;
    if (response.ok && payload?.approval_required === true) {
      return `Omnix approval required for this exact workspace ${toolName} action. Ask the user to approve it in Omnix, then retry the same action.`;
    }
    const detail = typeof payload?.detail === "string"
      ? payload.detail
      : typeof payload?.reason === "string" ? payload.reason : `HTTP ${response.status}`;
    return `Omnix workspace permission denied: ${detail}`;
  } catch (error) {
    return `Omnix workspace permission service unavailable: ${String(error)}`;
  }
}

async function authorizeTool(toolName: string): Promise<string | null> {
  if (!runId) return "Omnix run identity is missing.";
  try {
    const response = await fetch(`${brokerUrl}/${encodeURIComponent(runId)}/budget/tool`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Omnix-Client": "agent-runtime", Authorization: await runAuthorization(brokerUrl, runId) },
      body: JSON.stringify({ tool_name: toolName }),
    });
    if (response.ok) return null;
    let detail = "tool budget authorization failed";
    try {
      const payload = await response.json();
      detail = typeof payload?.detail === "string" ? payload.detail : JSON.stringify(payload);
    } catch {
      detail = await response.text();
    }
    return `Omnix budget blocked this tool call: ${detail}`;
  } catch (error) {
    return `Omnix budget authorization unavailable: ${String(error)}`;
  }
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    const input = (event as any).input || {};
    let pendingSearchContext: SearchPathContext | undefined;
    if (["read", "edit", "write", "grep", "find", "ls"].includes(event.toolName)) {
      const writablePathTool = event.toolName === "edit" || event.toolName === "write";
      let primaryPath: ToolPathResolution | undefined;
      for (const key of ["path", "file", "directory", "cwd"]) {
        const resolution = resolveIssuedToolPath(input[key], writablePathTool);
        if (resolution === null) {
          return {
            block: true,
            reason: "Omnix workspace policy blocked a path outside the issued roots or a mutation of a read-only reference root.",
          };
        }
        if (!resolution) continue;
        if (key === "path") primaryPath = resolution;
        if (resolution.qualified) input[key] = resolution.executionPath;
      }
      if (event.toolName === "grep") {
        const resolution = primaryPath || resolveIssuedToolPath(".", false);
        if (resolution) {
          const candidate = resolution.root.rootId === "workspace"
            ? path.resolve(workspace, resolution.executionPath)
            : resolution.executionPath;
          try {
            if (fs.statSync(candidate).isDirectory()) {
              pendingSearchContext = {
                root: resolution.root,
                searchRoot: resolution.rootRelative,
              };
            }
          } catch {
            // The built-in tool will report the missing path normally.
          }
        }
      }
      if (event.toolName === "edit" || event.toolName === "write") {
        const capabilityId = `workspace.${event.toolName}`;
        if (!localCapabilities.has(capabilityId)) {
          return { block: true, reason: `Omnix capability policy did not issue ${capabilityId}.` };
        }
        const planningRejection = await authorizePlanningOperation(event.toolName, input);
        if (planningRejection) return { block: true, reason: planningRejection };
      }
      if (
        (event.toolName === "edit" || event.toolName === "write")
        && approvalPolicy === "always_ask"
        && localCapabilities.has(`workspace.${event.toolName}`)
      ) {
        const permissionRejection = await authorizeWorkspaceTool(event.toolName, input);
        if (permissionRejection) return { block: true, reason: permissionRejection };
      }
    }
    if (event.toolName === "bash" || event.toolName === "powershell") {
      const safetyRejection = commandSafetyRejectionReason(input.command);
      if (safetyRejection) return { block: true, reason: safetyRejection };

      const commandAllowedByIssuedCapability = commandPrefixAllowed(input.command);
      if (!commandAllowedByIssuedCapability && !localCapabilities.has("workspace.command")) {
        const rejection = commandRejectionReason(input.command);
        if (rejection) return { block: true, reason: rejection };
      }

      // Capability/scope authority remains independent of planning. The
      // server classifies ordinary operations as free/advisory and reserves
      // hard plan authority for consequential mutations.
      const planningRejection = await authorizePlanningOperation(event.toolName, input);
      if (planningRejection) return { block: true, reason: planningRejection };

      const commandNeedsApproval = localCapabilities.has("workspace.command")
        && approvalPolicy !== "allow_automatic"
        && (approvalPolicy === "always_ask" || !commandAllowedByIssuedCapability || !sandboxed);
      if (commandNeedsApproval) {
        const permissionRejection = await authorizeBlockedCommand(input.command as string, input.cwd);
        if (permissionRejection) return { block: true, reason: permissionRejection };
      }
    }

    // Once the current working plan is approved, repeated broad discovery is
    // no longer free-form. Preserve long-horizon execution while preventing
    // a model from burning dozens of reads/searches without attempting the
    // planned mutation or focused validation.
    const progressRejection = await postPlanProgressRejection(event.toolName, input);
    if (progressRejection) return { block: true, reason: progressRejection };

    // The broker charges omnix_capability when it executes the call.
    if (event.toolName !== "omnix_capability") {
      const budgetError = await authorizeTool(event.toolName);
      if (budgetError) return { block: true, reason: budgetError };
    }
    if (pendingSearchContext) searchPathContexts.set(event.toolCallId, pendingSearchContext);
  });

  pi.on("tool_result", async (event) => {
    if (event.toolName !== "grep") return;
    const context = searchPathContexts.get(event.toolCallId);
    searchPathContexts.delete(event.toolCallId);
    if (!context || event.isError) return;
    let changed = false;
    let announced = false;
    const content = event.content.map((block) => {
      if (block.type !== "text") return block;
      const normalized = block.text
        .split("\n")
        .map((line) => normalizeGrepLine(line, context))
        .join("\n");
      if (normalized === block.text) return block;
      changed = true;
      const header = announced
        ? ""
        : `[Omnix path root: ${context.root.rootId}; grep paths normalized to root-qualified form]\n`;
      announced = true;
      return { ...block, text: header + normalized };
    });
    if (!changed) return;
    const existingDetails = event.details && typeof event.details === "object"
      ? event.details as Record<string, unknown>
      : {};
    return {
      content,
      details: {
        ...existingDetails,
        omnix_path_context: {
          root_id: context.root.rootId,
          search_root: context.searchRoot,
          normalized: true,
        },
      },
    };
  });
}

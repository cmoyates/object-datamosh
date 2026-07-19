import { createHash } from "node:crypto";
import { mkdir, open, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";

export const SCHEMA_VERSION = 1;
export const HARD_MAX_AGENT_CALLS = 32;
export const HARD_MAX_REVIEW_ROUNDS = 3;
export const HARD_MAX_STAGE_CONTINUATIONS = 2;
export const HARD_MAX_TRANSIENT_RETRIES = 3;
export const HANDOFF_MAX_BYTES = 8 * 1024;
export const CONTEXT_SOFT_LIMIT = 22;
export const CONTEXT_HARD_LIMIT = 28;
export const CONTEXT_ABSOLUTE_LIMIT = 30;

const SHA_RE = /^[0-9a-f]{40}$/;
const PR_URL_RE = /^https:\/\/github\.com\/[^/]+\/[^/]+\/pull\/\d+$/;
const STAGES = new Set(["implement", "review", "verify", "merge", "synchronize"]);
const FINAL_FAILURES = new Set([
  "review-budget-exhausted", "call-budget-exhausted", "continuation-budget-exhausted",
  "transient-retry-exhausted", "stale-review-sha", "stale-verification-sha",
  "stale-resume-state", "review-blocked", "verification-blocked", "deterministic-operation-failed",
]);

function integer(value, fallback, min, max) {
  return Number.isInteger(value) ? Math.max(min, Math.min(max, value)) : fallback;
}

export function parseWorkflowArgs(value) {
  const input = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const soft = integer(input.contextSoftLimit, CONTEXT_SOFT_LIMIT, 10, 24);
  const hard = integer(input.contextHardLimit, CONTEXT_HARD_LIMIT, soft + 1, 29);
  return Object.freeze({
    maxIssues: integer(input.maxIssues, 6, 1, 6),
    maxReviewRounds: integer(input.maxReviewRounds, 3, 1, HARD_MAX_REVIEW_ROUNDS),
    maxAgentCalls: integer(input.maxAgentCalls, HARD_MAX_AGENT_CALLS, 1, HARD_MAX_AGENT_CALLS),
    maxStageContinuations: integer(input.maxStageContinuations, 2, 0, HARD_MAX_STAGE_CONTINUATIONS),
    maxTransientRetries: integer(input.maxTransientRetries, 3, 1, HARD_MAX_TRANSIENT_RETRIES),
    contextSoftLimit: soft,
    contextHardLimit: hard,
  });
}

export function assertSha(value, field) {
  if (typeof value !== "string" || !SHA_RE.test(value)) throw new Error(`${field} must be a full lowercase git SHA`);
}

function requiredString(result, field) {
  if (typeof result?.[field] !== "string" || !result[field].trim()) throw new Error(`${field} is required`);
}

function requireIssue(result, expectedIssue) {
  if (result.issueNumber !== expectedIssue) throw new Error(`issueNumber must equal selected issue #${expectedIssue}`);
}

function validateHandoffField(result) { requiredString(result, "handoffPath"); }

export function validateImplementationResult(result, expected, live) {
  if (!result || !["implemented", "none", "paused", "failed"].includes(result.status)) throw new Error("invalid implementation status");
  requiredString(result, "summary");
  if (result.status !== "implemented") return result;
  for (const field of ["issueTitle", "baseSha", "implementationSha", "branchName", "pullRequestUrl", "handoffPath"]) requiredString(result, field);
  requireIssue(result, expected.issueNumber);
  const branch = `agent/issue-${expected.issueNumber}`;
  if (result.branchName !== branch) throw new Error(`branchName must equal ${branch}`);
  assertSha(result.baseSha, "baseSha"); assertSha(result.implementationSha, "implementationSha");
  if (!PR_URL_RE.test(result.pullRequestUrl)) throw new Error("pullRequestUrl is invalid");
  if (!live?.exists || live.state !== "OPEN") throw new Error("expected PR is not open");
  if (live.baseBranch !== "main") throw new Error("PR must target main");
  if (live.headBranch !== branch) throw new Error("PR head branch is unexpected");
  if (live.headSha !== result.implementationSha) throw new Error("implementation SHA differs from live PR head");
  if (!live.treeClean) throw new Error("working tree is not clean");
  return result;
}

function validateFindings(result) {
  if (!Array.isArray(result.findings)) throw new Error("findings is required");
  const ids = new Set();
  for (const finding of result.findings) {
    requiredString(finding, "id"); requiredString(finding, "status");
    if (ids.has(finding.id)) throw new Error(`duplicate finding id ${finding.id}`);
    ids.add(finding.id);
  }
  if (!Array.isArray(result.affectedPaths)) throw new Error("affectedPaths is required");
  if (!Array.isArray(result.verificationReceipts)) throw new Error("verificationReceipts is required");
}

export function validateReviewResult(result, expected, live) {
  if (!result || !["clean", "fixed", "blocked", "paused", "failed"].includes(result.status)) throw new Error("invalid review status");
  requiredString(result, "summary"); requireIssue(result, expected.issueNumber);
  if (!["clean", "fixed"].includes(result.status)) return result;
  for (const field of ["reviewedSha", "finalSha", "handoffPath"]) requiredString(result, field);
  assertSha(result.reviewedSha, "reviewedSha"); assertSha(result.finalSha, "finalSha");
  validateFindings(result);
  if (result.reviewedSha !== expected.expectedHeadSha) throw new Error("reviewed SHA is stale");
  if (result.finalSha !== live?.headSha) throw new Error("final SHA differs from live PR head");
  if (!live.treeClean) throw new Error("review left an uncommitted working tree");
  if (result.status === "clean") {
    if (result.reviewedSha !== result.finalSha) throw new Error("clean review must not change SHA");
    if (result.findings.some((finding) => finding.status === "open" || finding.status === "blocked")) throw new Error("clean review has unresolved blockers");
  } else {
    if (result.finalSha === result.reviewedSha) throw new Error("fixed review must change SHA");
    if (!live.remoteEqualsLocal) throw new Error("fixed review SHA was not pushed");
    if (!result.findings.some((finding) => finding.status === "fixed")) throw new Error("fixed review requires a fixed current-issue finding");
    if (result.approved === true) throw new Error("a fixing round cannot approve itself");
  }
  return result;
}

export function validateVerificationResult(result, expected, live) {
  if (!result || !["verified", "blocked", "paused", "failed"].includes(result.status)) throw new Error("invalid verification status");
  requiredString(result, "summary"); requireIssue(result, expected.issueNumber);
  if (result.status !== "verified") return result;
  for (const field of ["verifiedSha", "pullRequestUrl"]) requiredString(result, field);
  assertSha(result.verifiedSha, "verifiedSha");
  if (!Array.isArray(result.verificationReceipts) || result.verificationReceipts.length === 0) throw new Error("exact verification receipts are required");
  if (result.verifiedSha !== expected.cleanReviewSha) throw new Error("verification SHA differs from clean-review SHA");
  if (result.verifiedSha !== live?.headSha) throw new Error("verification SHA differs from live PR head");
  if (!live.treeClean) throw new Error("verification working tree is not clean");
  if (result.merged || result.closedIssue) throw new Error("verification agent must not merge or close");
  return result;
}

export function validateMergeInvariants(state, live) {
  assertSha(state.cleanReviewSha, "cleanReviewSha"); assertSha(state.verifiedSha, "verifiedSha");
  if (state.cleanReviewSha !== state.verifiedSha) throw new Error("stale-verification-sha");
  if (live.headSha !== state.verifiedSha) throw new Error("PR head changed between verification and merge");
  if (live.state !== "OPEN" || live.baseBranch !== "main" || live.headBranch !== state.branchName) throw new Error("PR identity changed before merge");
  if (!live.issueOpen || !live.treeClean || !live.remoteEqualsLocal || !live.requiredChecksPassed) throw new Error("merge prerequisites failed");
  return true;
}

export function classifyTransientAgentError(error) {
  const text = String(error?.message ?? error ?? "").toLowerCase();
  return /(sse.*(header|timeout)|websocket.*(disconnect|closed|error)|econnreset|etimedout|http\s*429|status\s*429|http\s*5\d\d|status\s*5\d\d|bad gateway|service unavailable|gateway timeout)/i.test(text);
}

export function enforceAgentCallBudget(state, limit = HARD_MAX_AGENT_CALLS) {
  if ((state.agentCallsUsed ?? 0) >= limit) return { ok: false, status: "call-budget-exhausted" };
  state.agentCallsUsed = (state.agentCallsUsed ?? 0) + 1;
  return { ok: true, used: state.agentCallsUsed, limit };
}

export async function reliableAgent(call, options) {
  const { state, limits, persist = async () => {}, verifyState = async () => {}, delay = async () => {}, random = Math.random } = options;
  for (let attempt = 1; attempt <= limits.maxTransientRetries; attempt += 1) {
    const budget = enforceAgentCallBudget(state, limits.maxAgentCalls);
    if (!budget.ok) return { ok: false, terminalStatus: budget.status, error: budget.status };
    state.attempt = attempt; await persist(state);
    let outcome;
    try { outcome = await call({ attempt, continuation: state.continuationNumber ?? 0 }); }
    catch (error) { outcome = { ok: false, error: String(error?.message ?? error) }; }
    if (outcome.ok || !classifyTransientAgentError(outcome.error)) return outcome;
    state.retriesUsed = (state.retriesUsed ?? 0) + 1;
    state.lastFailure = { kind: "transient", message: outcome.error, at: new Date().toISOString() };
    await persist(state);
    if (attempt === limits.maxTransientRetries) return { ...outcome, terminalStatus: "transient-retry-exhausted" };
    await verifyState(state);
    await delay(Math.min(2000, 100 * 2 ** (attempt - 1)) + Math.floor(random() * 50));
  }
}

export function calculateReviewScope({ round, fullReviewBaseSha, expectedHeadSha, previousReviewedSha, priorFindingIds = [], affectedPaths = [], handoffPath }) {
  return Object.freeze({
    mode: round === 1 ? "full" : "delta",
    fullReviewBaseSha,
    expectedHeadSha,
    deltaFromSha: round === 1 ? undefined : previousReviewedSha,
    priorFindingIds: [...priorFindingIds], affectedPaths: [...affectedPaths], round, handoffPath,
  });
}

export function calculateResumeStage(state, live) {
  validateWorkflowState(state);
  if (live.prMerged) return live.issueClosed ? "synchronize" : "synchronize";
  if (!live.prOpen || live.baseBranch !== "main" || live.headBranch !== state.branchName) throw new Error("stale-resume-state");
  if (state.verifiedSha) return "merge";
  if (state.cleanReviewSha) return "verify";
  if (state.implementationSha || state.stage === "review") return "review";
  return "implement";
}

export function calculateNextStage({ stage, status, reviewRound, maxReviewRounds }) {
  if (stage === "implement") return status === "implemented" ? { stage: "review", reviewRound: 1 } : { stage, terminal: status };
  if (stage === "review") {
    if (status === "clean") return { stage: "verify", reviewRound };
    if (status === "paused") return { stage: "review", reviewRound };
    if (status === "fixed" && reviewRound >= maxReviewRounds) return { stage: "review", terminal: "review-budget-exhausted", reviewRound };
    if (status === "fixed") return { stage: "review", reviewRound: reviewRound + 1 };
    return { stage: "review", terminal: status === "blocked" ? "review-blocked" : status, reviewRound };
  }
  if (stage === "verify") return status === "verified" ? { stage: "merge" } : { stage, terminal: status === "blocked" ? "verification-blocked" : status };
  if (stage === "merge") return { stage: "synchronize" };
  return { stage };
}

export function selectNextIssue(issues) {
  const open = issues.filter((issue) => issue.state === "OPEN" && !issue.isPullRequest);
  const labelled = open.filter((issue) => (issue.labels ?? []).some((label) => String(label).toLowerCase() === "ready-for-agent"));
  const eligible = labelled.length ? labelled : open;
  const unblocked = eligible.filter((issue) => Number(issue.openBlockerCount ?? 0) === 0);
  return [...unblocked].sort((a, b) => Number(b.blocksEligibleCount ?? 0) - Number(a.blocksEligibleCount ?? 0) || a.number - b.number)[0];
}

export function dedupeFollowUpCandidates(candidates, existingMarkers = new Set(), limit = 2) {
  const selected = [];
  const seen = new Set();
  for (const candidate of candidates) {
    if (!candidate || typeof candidate.id !== "string" || !candidate.id.trim() || seen.has(candidate.id) || existingMarkers.has(candidate.id)) continue;
    seen.add(candidate.id); selected.push(candidate);
    if (selected.length === limit) break;
  }
  return selected;
}

export function followUpMarker(id) { return `<!-- pi-workflow-followup-id:${id} -->`; }

export function contextDecision(percent, policy = { soft: CONTEXT_SOFT_LIMIT, hard: CONTEXT_HARD_LIMIT }) {
  if (!Number.isFinite(percent)) return "continue";
  if (percent >= CONTEXT_ABSOLUTE_LIMIT) return "abort";
  if (percent >= policy.hard) return "persist-only";
  if (percent >= policy.soft) return "finish-atomic-and-pause";
  return "continue";
}

export function buildAgentPolicy(stage, args) {
  const common = ["read", "rg", "fd", "bash", "edit", "write", "structured_output"];
  return {
    allowedTools: common,
    excludedTools: ["workflow", "subagent_spawn", "subagent_wait", "subagent_cancel", "subagent_check", "subagent_list", "bg_start", "bg_status", "bg_list", "bg_kill", "ask_user"],
    contextBudget: { softPercent: args.contextSoftLimit, hardPercent: args.contextHardLimit, requireHandoffOnPause: true, maximumPercent: CONTEXT_ABSOLUTE_LIMIT },
    stage,
  };
}

export function validateWorkflowState(state) {
  if (!state || typeof state !== "object" || Array.isArray(state)) throw new Error("workflow state must be an object");
  if (state.schemaVersion !== SCHEMA_VERSION) throw new Error("unsupported workflow state schemaVersion");
  requiredString(state, "repository"); requiredString(state, "stage");
  if (!STAGES.has(state.stage)) throw new Error("invalid workflow stage");
  if (state.issueNumber !== undefined && (!Number.isInteger(state.issueNumber) || state.issueNumber < 1)) throw new Error("invalid state issueNumber");
  for (const field of ["agentCallsUsed", "retriesUsed", "continuationsUsed"]) if (state[field] !== undefined && (!Number.isInteger(state[field]) || state[field] < 0)) throw new Error(`invalid ${field}`);
  return state;
}

export async function loadWorkflowState(path, fs = { readFile, rename }) {
  try { return validateWorkflowState(JSON.parse(await fs.readFile(path, "utf8"))); }
  catch (error) {
    if (error?.code === "ENOENT") return undefined;
    const corrupt = `${path}.corrupt-${Date.now()}`;
    try { await fs.rename(path, corrupt); } catch {}
    const failure = new Error(`stale-resume-state: state is corrupt; preserved at ${corrupt}`); failure.cause = error; throw failure;
  }
}

export async function saveWorkflowStateAtomic(path, state, fs = { mkdir, writeFile, rename, open }) {
  validateWorkflowState(state);
  await fs.mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.tmp-${process.pid}-${Date.now()}`;
  await fs.writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  if (fs.open) { const handle = await fs.open(temporary, "r"); try { await handle.sync(); } finally { await handle.close(); } }
  await fs.rename(temporary, path);
}

export async function clearWorkflowState(path, fs = { rm }) { await fs.rm(path, { force: true }); }

export async function enforceHandoffLimit(path, expected, root, fs = { readFile, stat }) {
  const resolvedPath = resolve(path); const resolvedRoot = resolve(root);
  if (!(resolvedPath === resolvedRoot || resolvedPath.startsWith(`${resolvedRoot}${sep}`))) throw new Error("handoff path escapes workflow state directory");
  const info = await fs.stat(resolvedPath); if (info.size > HANDOFF_MAX_BYTES) throw new Error("handoff exceeds 8 KB limit");
  const parsed = JSON.parse(await fs.readFile(resolvedPath, "utf8"));
  if (parsed.schemaVersion !== SCHEMA_VERSION || parsed.issueNumber !== expected.issueNumber || parsed.stage !== expected.stage) throw new Error("handoff identity does not match expected issue/stage");
  return parsed;
}

export async function buildCommandReceipt({ command, exitCode, durationMs, output, logPath, write = writeFile, mkdirFn = mkdir, failureExcerptBytes = 2048 }) {
  await mkdirFn(dirname(logPath), { recursive: true }); await write(logPath, output, "utf8");
  const hash = createHash("sha256").update(output).digest("hex");
  const lines = output.trim().split(/\r?\n/); const summary = exitCode === 0 ? `${command}: PASS — ${lines.at(-1) || "completed"} — full log: ${logPath}` : `${command}: FAIL (exit ${exitCode}) — full log: ${logPath}`;
  return { command, exitCode, durationMs, logPath, logSha256: hash, summary, failureExcerpt: exitCode === 0 ? undefined : output.slice(-failureExcerptBytes) };
}

export function quoteUntrustedData(value) { return JSON.stringify(String(value)); }

export function isTerminalFailure(value) { return FINAL_FAILURES.has(value); }

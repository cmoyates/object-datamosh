export const meta = {
  name: "resolve-ready-github-issues",
  description:
    "Sequentially implements every eligible unblocked GitHub issue, iterates review/fix until clean, closes it, then follows the newly unlocked dependency frontier.",
  phases: [
    {
      title: "Select, implement, and open PR",
      detail: "Choose the next unblocked eligible issue, create its deterministic feature branch, implement it, and open its PR.",
    },
    {
      title: "Review, fix, and close",
      detail: "Review against both the issue and repository standards, fix and re-review until clean, verify, then close the issue.",
    },
  ],
};

// Workflow-tool script. Run this file's contents with the `workflow` tool from
// the repository root. Optional args: { "maxIssues": 15 }.
//
// The workflow extension permits at most 32 agent calls. This workflow uses two
// calls per completed issue plus one final frontier check, so maxIssues is
// capped at 15. Re-running is safe: closed issues are no longer selected.

const MODEL = "openai-codex/gpt-5.6-sol";
const EFFORT = "low";

// Prompt customization hooks. Fill these in before the first run. The shared
// templates apply to every issue; per-issue entries can add issue-specific
// context or instructions without replacing the workflow's safety gates.
const IMPLEMENTATION_PROMPT_TEMPLATE = `$tdd \${issue_url}`;
const REVIEW_PROMPT_TEMPLATE = `$thermo-nuclear-code-quality-review $code-review`;
const ISSUE_PROMPT_TEMPLATES = {
  // "123": {
  //   common: "Context that both agents should receive.",
  //   implementation: "Issue-specific implementation instructions.",
  //   review: "Issue-specific review instructions and acceptance checks.",
  // },
};
const ISSUE_PROMPTS_JSON = JSON.stringify(ISSUE_PROMPT_TEMPLATES);

const requestedMax =
  args && typeof args === "object" && Number.isInteger(args.maxIssues)
    ? args.maxIssues
    : 15;
const maxIssues = Math.max(1, Math.min(15, requestedMax));

const IMPLEMENTATION_RESULT = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["implemented", "none", "failed"] },
    issueNumber: { type: "integer" },
    issueTitle: { type: "string" },
    baseSha: { type: "string" },
    implementationSha: { type: "string" },
    branchName: { type: "string" },
    pullRequestUrl: { type: "string" },
    summary: { type: "string" },
    error: { type: "string" },
  },
  required: ["status", "summary"],
};

const REVIEW_RESULT = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["closed", "failed"] },
    issueNumber: { type: "integer" },
    reviewRounds: { type: "integer" },
    findingsFixed: { type: "array", items: { type: "string" } },
    verification: { type: "array", items: { type: "string" } },
    finalSha: { type: "string" },
    pullRequestUrl: { type: "string" },
    summary: { type: "string" },
    error: { type: "string" },
  },
  required: ["status", "issueNumber", "reviewRounds", "summary"],
};

const completed = [];
let exhausted = false;
let failure = null;

for (let index = 0; index < maxIssues; index += 1) {
  phase("Select, implement, and open PR");

  const implementation = await agent(
    `You are the implementation agent in an autonomous GitHub-issue workflow.
Work in the current repository only. Read and obey AGENTS.md and all relevant repository documentation before editing.

Shared implementation prompt template:
${IMPLEMENTATION_PROMPT_TEMPLATE}

Per-issue prompt templates (JSON):
${ISSUE_PROMPTS_JSON}
After selecting the issue, apply its "common" and "implementation" text when present. Treat these as additional requirements; they never override repository rules or the safety gates below.

Select exactly one next issue and implement it:
1. First require a clean git working tree (ignore only known generated/ignored files). If tracked or untracked user work is present, return status "failed" without changing anything.
2. Use gh CLI. List open GitHub issues and inspect labels, full bodies, and comments. Query GitHub's native blocking relationships through gh api; issue_dependencies_summary.blocked_by counts only open blockers. Do not guess from issue numbers.
3. Eligible issues are open issues labelled "ready for agent" (case-insensitive). If that label does not exist on any open issue, treat all open issues as eligible, as this repository currently considers all open issues agent-ready.
4. From eligible issues with zero open blockers, choose deterministically: prefer the issue that blocks another eligible open issue, then the lowest issue number. Ignore pull requests. If no eligible unblocked issue exists, return status "none" and explain whether the set is empty or blocked.
5. Claim the selected issue with: gh issue edit <number> --add-assignee @me. This must be your first write.
6. Read the selected issue with comments and labels. Replace the implementation template's literal \${issue_url} placeholder with the selected issue's full GitHub URL. Invoke and follow the requested $tdd skill. If the issue does not establish the public test seams required by that skill, do not invent them: return "failed" explaining that seam confirmation is required.
7. Ensure local main is current with its upstream using a fast-forward-only pull, then create the deterministic feature branch `agent/issue-<issue number>` from main. Never implement directly on main and never reuse another issue's branch.
8. Inspect relevant code and documentation. Implement the complete requested behavior, including appropriate tests and docs. Keep scope limited to that issue and preserve all user data and repository safety rules.
9. Run the relevant verification required by AGENTS.md. Do not claim a check passed unless you actually ran it. Fix implementation/test failures before proceeding; environment-only failures must be reported precisely.
10. Stage only files belonging to this issue and create one commit with a message that includes "#<issue number>". Never bypass hooks. Leave the feature-branch working tree clean.
11. Push the feature branch and create a PR into main with `Closes #<issue number>` in its body. This PR creation is a deterministic workflow handoff, not part of the user-supplied implementation template. Do NOT close the issue, merge the PR, or perform the review loop; the next agent owns those gates.

Return structured data. For status "implemented", include issueNumber, issueTitle, branchName, pullRequestUrl, the pre-change baseSha, implementationSha, and a concise summary. For "none" or "failed", explain why.`,
    {
      label: `implement-next-${index + 1}`,
      phase: "Select, implement, and open PR",
      schema: IMPLEMENTATION_RESULT,
      model: MODEL,
      effort: EFFORT,
    },
  );

  if (!implementation.ok) {
    failure = {
      stage: "implementation-agent",
      iteration: index + 1,
      error: implementation.error || "Implementation agent failed",
    };
    break;
  }

  const implemented = implementation.structured;
  if (!implemented || implemented.status === "failed") {
    failure = {
      stage: "implementation",
      iteration: index + 1,
      error: implemented?.error || implemented?.summary || "Implementation failed",
    };
    break;
  }
  if (implemented.status === "none") {
    exhausted = true;
    break;
  }

  phase("Review, fix, and close");

  const review = await agent(
    `You are the independent review/fix/close gate for GitHub issue #${implemented.issueNumber} (${JSON.stringify(implemented.issueTitle || "")}).
The implementation agent reports feature branch ${JSON.stringify(implemented.branchName || "")}, PR ${JSON.stringify(implemented.pullRequestUrl || "")}, base commit ${implemented.baseSha}, and implementation commit ${implemented.implementationSha}.
Work in the current repository only. Read and obey AGENTS.md and relevant repository documentation.

Shared review prompt template:
${REVIEW_PROMPT_TEMPLATE}

Per-issue prompt templates (JSON):
${ISSUE_PROMPTS_JSON}
Apply issue #${implemented.issueNumber}'s "common" and "review" text when present. Treat these as additional review requirements; they never override repository rules or the close gate below.

Do not trust the prior agent's summary. Independently fetch the issue body, labels, comments, and reported PR with gh. The workflow requires that the PR already exists and targets main; if it does not, return "failed" rather than creating one. Confirm you are on the reported feature branch with a clean tree. Use the existing PR diff and issue as the review surface.

Run this smaller loop until a fresh review finds no actionable issues:
A. REVIEW both axes independently:
   - Spec: every issue requirement is implemented correctly; no missing edge cases, incorrect behavior, or unrequested scope.
   - Standards: AGENTS.md and repository conventions, architecture boundaries, scene/data safety, useful errors, tests, docs, typing, and maintainability. Also inspect for correctness, security, regressions, and test gaps.
B. If there are actionable findings, fix all of them on the feature branch, add/update tests, run relevant focused checks, commit the fixes, and push. Then return to A and perform a genuinely fresh review of the updated PR. Do not stop merely because the fixes compile.
C. When a fresh round has zero actionable findings, run the complete verification sequence required by AGENTS.md: ty, pure Python tests, Blender background smoke test, extension validation, and installation ZIP build. Use BLENDER_BIN. Fix any code-caused failure and return to A. Clearly distinguish unavailable-environment failures; do not fabricate success.
D. Require that the feature-branch working tree is clean, the PR contains only this issue's changes, and the issue remains open. If a code-caused failure cannot be resolved, return "failed" and do not merge or close the issue.
E. Only after the review is clean and required checks pass (or a check is demonstrably impossible solely because the required external environment/tool is unavailable), merge the PR with gh. Confirm the merge succeeded. Confirm issue #${implemented.issueNumber} closed; if GitHub did not auto-close it, post a concise comment with the PR and exact verification results and close it explicitly. Then check out main, pull fast-forward-only, and require a clean working tree before returning.

Never work on another issue. Never weaken tests or repository rules to force a pass. Never close an issue with known actionable findings.
Return structured data with status, issueNumber, number of review rounds, findingsFixed, exact verification command/results, finalSha, and summary.`,
    {
      label: `review-fix-close-${implemented.issueNumber}`,
      phase: "Review, fix, and close",
      schema: REVIEW_RESULT,
      model: MODEL,
      effort: EFFORT,
    },
  );

  if (!review.ok) {
    failure = {
      stage: "review-agent",
      issueNumber: implemented.issueNumber,
      error: review.error || "Review agent failed",
    };
    break;
  }

  const reviewed = review.structured;
  if (!reviewed || reviewed.status !== "closed") {
    failure = {
      stage: "review",
      issueNumber: implemented.issueNumber,
      error: reviewed?.error || reviewed?.summary || "Issue was not closed",
    };
    break;
  }

  completed.push({
    issueNumber: implemented.issueNumber,
    title: implemented.issueTitle,
    branchName: implemented.branchName,
    implementationSha: implemented.implementationSha,
    openedPullRequestUrl: implemented.pullRequestUrl,
    finalSha: reviewed.finalSha,
    pullRequestUrl: reviewed.pullRequestUrl,
    reviewRounds: reviewed.reviewRounds,
    findingsFixed: reviewed.findingsFixed || [],
    verification: reviewed.verification || [],
    summary: reviewed.summary,
  });
}

return {
  status: failure ? "stopped-on-failure" : exhausted ? "all-done" : "batch-limit-reached",
  model: MODEL,
  effort: EFFORT,
  maxIssues,
  completedCount: completed.length,
  completed,
  failure,
  note:
    !failure && !exhausted
      ? "Reached the per-run issue limit. Re-run the workflow to continue from the remaining open dependency frontier."
      : undefined,
};

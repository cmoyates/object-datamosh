export const meta = {
  name: "resolve-ready-github-issues",
  description:
    "Implements the unblocked issue frontier, reviews in fresh bounded contexts, hands off between rounds, and extracts genuinely out-of-scope findings into follow-up tickets.",
  phases: [
    {
      title: "Select, implement, and open PR",
      detail: "Choose the next unblocked issue, implement it, open its PR, and write a handoff.",
    },
    {
      title: "Review and fix",
      detail: "Run bounded review/fix rounds in fresh contexts, handing off between each round.",
    },
    {
      title: "Verify, merge, and close",
      detail: "Run the complete verification gate, merge the clean PR, and close its issue.",
    },
  ],
};

// Workflow-tool script. Run this file's contents with the `workflow` tool from
// the repository root. Optional args: { "maxIssues": 6, "maxReviewRounds": 3 }.
//
// Each issue uses at most five calls: implementation, three bounded review/fix
// rounds, and final verification. Six issues plus one frontier check fit within
// the workflow tool's 32-call limit. Re-running is safe because closed issues
// are no longer selected.

const MODEL = "openai-codex/gpt-5.6-sol";
const EFFORT = "low";
const HARD_MAX_ISSUES = 6;
const HARD_MAX_REVIEW_ROUNDS = 3;

const requestedMaxIssues =
  args && typeof args === "object" && Number.isInteger(args.maxIssues)
    ? args.maxIssues
    : HARD_MAX_ISSUES;
const maxIssues = Math.max(1, Math.min(HARD_MAX_ISSUES, requestedMaxIssues));
const requestedReviewRounds =
  args && typeof args === "object" && Number.isInteger(args.maxReviewRounds)
    ? args.maxReviewRounds
    : HARD_MAX_REVIEW_ROUNDS;
const maxReviewRounds = Math.max(
  1,
  Math.min(HARD_MAX_REVIEW_ROUNDS, requestedReviewRounds),
);

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
    handoffPath: { type: "string" },
    summary: { type: "string" },
    error: { type: "string" },
  },
  required: ["status", "summary"],
};

const REVIEW_RESULT = {
  type: "object",
  properties: {
    status: {
      type: "string",
      enum: ["clean", "fixed", "blocked", "failed"],
    },
    issueNumber: { type: "integer" },
    reviewedSha: { type: "string" },
    finalSha: { type: "string" },
    findingsFixed: { type: "array", items: { type: "string" } },
    followUpIssues: { type: "array", items: { type: "string" } },
    remainingBlockers: { type: "array", items: { type: "string" } },
    handoffPath: { type: "string" },
    summary: { type: "string" },
    error: { type: "string" },
  },
  required: ["status", "issueNumber", "summary"],
};

const FINAL_RESULT = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["closed", "blocked", "failed"] },
    issueNumber: { type: "integer" },
    verification: { type: "array", items: { type: "string" } },
    finalSha: { type: "string" },
    pullRequestUrl: { type: "string" },
    handoffPath: { type: "string" },
    summary: { type: "string" },
    error: { type: "string" },
  },
  required: ["status", "issueNumber", "summary"],
};

const completed = [];
let exhausted = false;
let failure = null;

for (let index = 0; index < maxIssues; index += 1) {
  phase("Select, implement, and open PR");

  const implementation = await agent(
    `You are the implementation agent in an autonomous GitHub-issue workflow.
Work only in the current repository. Read and obey AGENTS.md and all relevant repository documentation.

Select and implement exactly one issue:
1. Require a clean git working tree before any write. If user work is present, return "failed" without changing anything.
2. Use gh to inspect open issues, labels, full bodies, comments, and GitHub native dependencies. Eligible issues are open and labelled ready-for-agent (case-insensitive). If no open issue has that label, treat all open issues as eligible.
3. Among eligible issues with zero open blockers, prefer one that blocks another eligible issue, then the lowest issue number. Ignore pull requests. Return "none" if the frontier is empty or entirely blocked.
4. Claim the issue with gh issue edit <number> --add-assignee @me as your first write.
5. Read the issue fully and invoke $tdd with its full GitHub URL. Do not invent a public test seam that the issue leaves undecided.
6. Fast-forward main and create agent/issue-<number> from main. Never implement on main or reuse another issue branch.
7. Implement only the selected issue, including tests and documentation. Run the relevant AGENTS.md verification and fix code failures.
8. Commit only scoped files with #<number> in the message, push, and open a PR to main containing Closes #<number>. Do not review, merge, or close it.
9. Invoke $handoff with the argument "Review the implementation for issue #<number> and its PR". The skill must write the handoff into the OS temporary directory. Return its absolute path. Do not copy the issue, diff, or commits into the handoff; reference their URLs and SHAs.

If context pressure appears at any point, stop before losing state, invoke $handoff for the exact next action, push any coherent committed work, and return "failed" with the handoff path rather than continuing until context exhaustion.

Return structured data. For "implemented", include the issue number/title, branch, PR URL, base SHA, implementation SHA, handoff path, and summary.`,
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
      handoffPath: implemented?.handoffPath,
      error: implemented?.error || implemented?.summary || "Implementation failed",
    };
    break;
  }
  if (implemented.status === "none") {
    exhausted = true;
    break;
  }

  let handoffPath = implemented.handoffPath || "";
  let clean = false;
  const findingsFixed = [];
  const followUpIssues = [];
  let roundsUsed = 0;

  for (let round = 1; round <= maxReviewRounds; round += 1) {
    phase("Review and fix");
    roundsUsed = round;

    const review = await agent(
      `You own exactly one bounded review/fix round for issue #${implemented.issueNumber} and PR ${implemented.pullRequestUrl} on branch ${implemented.branchName}.
Work only in the current repository. Read and obey AGENTS.md and relevant docs. Read the prior handoff at ${JSON.stringify(handoffPath)} when it exists, but independently verify every claim against GitHub and git. Invoke $thermo-nuclear-code-quality-review and $code-review.

Perform one fresh review pass across Spec and Standards. Classify every actionable finding:

CURRENT-ISSUE BLOCKER:
- violates an acceptance criterion;
- is a regression, correctness failure, data-loss/cleanup risk, or safety issue;
- or was directly introduced by this PR.
These findings may not be deferred. Fix all of them on the feature branch, add tests, run focused checks, commit with #${implemented.issueNumber}, and push. Do not perform another full review in this context; the next agent supplies the fresh pass.

FOLLOW-UP CANDIDATE:
- exposes a broader pre-existing architectural weakness;
- requires redesign outside the promised behavior;
- is maintainability work rather than required correctness;
- or expands substantially into another subsystem.
Do not implement it in this PR. Invoke $to-tickets to formulate and publish the smallest tracer-bullet follow-up issue or issue set. The maintainer has pre-approved publication of genuinely out-of-scope review findings through this workflow, so the skill's approval checkpoint is satisfied for this narrow case. Link the source issue and PR, apply ready-for-agent, and use native dependencies only when a real blocking relationship exists. Do not modify or close the source issue.

Return "clean" only when this fresh pass found no current-issue blocker. Return "fixed" after fixing one or more blockers; a fresh context must re-review. Return "blocked" without merging when an in-scope blocker cannot fit safely in this bounded round or requires a maintainer decision.

At the end, invoke $handoff with the argument "Continue review of issue #${implemented.issueNumber}, PR ${implemented.pullRequestUrl}, after review round ${round}". Save it in the OS temporary directory and return its absolute path. Keep it compact and reference the issue, PR, commits, tests, and follow-up issues rather than duplicating them. If context pressure appears, create this handoff early and stop safely.

Never merge or close the source issue in this round.`,
      {
        label: `review-fix-${implemented.issueNumber}-round-${round}`,
        phase: "Review and fix",
        schema: REVIEW_RESULT,
        model: MODEL,
        effort: EFFORT,
      },
    );

    if (!review.ok) {
      failure = {
        stage: "review-agent",
        issueNumber: implemented.issueNumber,
        round,
        handoffPath,
        error: review.error || "Review agent failed",
      };
      break;
    }

    const reviewed = review.structured;
    if (!reviewed || reviewed.status === "failed") {
      failure = {
        stage: "review",
        issueNumber: implemented.issueNumber,
        round,
        handoffPath: reviewed?.handoffPath || handoffPath,
        error: reviewed?.error || reviewed?.summary || "Review failed",
      };
      break;
    }

    handoffPath = reviewed.handoffPath || handoffPath;
    findingsFixed.push(...(reviewed.findingsFixed || []));
    followUpIssues.push(...(reviewed.followUpIssues || []));

    if (reviewed.status === "blocked") {
      failure = {
        stage: "review-blocked",
        issueNumber: implemented.issueNumber,
        round,
        handoffPath,
        remainingBlockers: reviewed.remainingBlockers || [],
        error: reviewed.summary,
      };
      break;
    }
    if (reviewed.status === "clean") {
      clean = true;
      break;
    }
  }

  if (failure) break;

  if (!clean) {
    failure = {
      stage: "review-budget-exhausted",
      issueNumber: implemented.issueNumber,
      roundsUsed,
      handoffPath,
      error:
        "The bounded review budget ended after a fix round. The PR remains open and requires a fresh review context; no finding was waived.",
    };
    break;
  }

  phase("Verify, merge, and close");

  const finalization = await agent(
    `You are the final verification and close gate for issue #${implemented.issueNumber} and PR ${implemented.pullRequestUrl} on branch ${implemented.branchName}.
Read and obey AGENTS.md. Read the latest handoff at ${JSON.stringify(handoffPath)} when it exists, but independently verify GitHub and git state.

Do not conduct an open-ended refactor or review loop. Confirm the latest fresh review was clean, the issue remains open, the PR targets main, the branch is clean, and the diff is issue-scoped. Run the complete verification required by AGENTS.md: ty, pure Python tests, Blender background smoke using BLENDER_BIN, extension validation, and installation ZIP build, plus any issue-specific checks. Do not fabricate unavailable checks.

If verification reveals a code defect or unmet acceptance criterion, do not fix, merge, or close in this context. Invoke $handoff with "Fix the final verification blocker for issue #${implemented.issueNumber} and PR ${implemented.pullRequestUrl}", return "blocked", and leave the exact failing command and next action in the handoff. A fresh workflow run must fix and re-review it.

Only when all gates pass (or a check is demonstrably impossible solely because its external environment is unavailable and the issue permits that limitation), merge the PR with gh. Confirm the issue closed; if GitHub did not auto-close it, post exact verification results and close it. Check out main, pull fast-forward-only, and require a clean working tree.

Invoke $handoff only on blocked or failed exit; successful closure needs no handoff. Return exact verification results, final SHA, PR URL, and summary.`,
    {
      label: `verify-merge-close-${implemented.issueNumber}`,
      phase: "Verify, merge, and close",
      schema: FINAL_RESULT,
      model: MODEL,
      effort: EFFORT,
    },
  );

  if (!finalization.ok) {
    failure = {
      stage: "finalization-agent",
      issueNumber: implemented.issueNumber,
      handoffPath,
      error: finalization.error || "Finalization agent failed",
    };
    break;
  }

  const finalized = finalization.structured;
  if (!finalized || finalized.status !== "closed") {
    failure = {
      stage: "finalization",
      issueNumber: implemented.issueNumber,
      handoffPath: finalized?.handoffPath || handoffPath,
      error: finalized?.error || finalized?.summary || "Issue was not closed",
    };
    break;
  }

  completed.push({
    issueNumber: implemented.issueNumber,
    title: implemented.issueTitle,
    branchName: implemented.branchName,
    implementationSha: implemented.implementationSha,
    pullRequestUrl: finalized.pullRequestUrl || implemented.pullRequestUrl,
    finalSha: finalized.finalSha,
    reviewRounds: roundsUsed,
    findingsFixed,
    followUpIssues,
    verification: finalized.verification || [],
    summary: finalized.summary,
  });
}

return {
  status: failure
    ? "stopped-on-failure"
    : exhausted
      ? "all-done"
      : "batch-limit-reached",
  model: MODEL,
  effort: EFFORT,
  maxIssues,
  maxReviewRounds,
  completedCount: completed.length,
  completed,
  failure,
  note:
    !failure && !exhausted
      ? "Reached the per-run issue limit. Re-run to continue from the remaining dependency frontier."
      : undefined,
};

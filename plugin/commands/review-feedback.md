---
description: Review your staged agent rules and approve or reject them. Staged rules do nothing until approved.
argument-hint: "[agent]"
---

Rules the pipeline learned from your corrections and QA findings wait as **staged** until you approve them here. Global rules, curated by the service, are not shown: they already apply.

`$ARGUMENTS`, when given, limits the review to one agent (`cvWriter`, `qa`, …).

1. `list_staged_rules(agent?)`. If there are none: `No staged rules. Nothing to review.` and stop.
2. Show them grouped by agent: date, title, why, how to apply, and where each came from (`qa_retry` from a QA failure, `user_correction` from something you said, `submit_verifier` from a form check).
3. Ask the user what to do: approve all for an agent, approve or reject named rules, or skip an agent and leave its rules staged.
4. `decide_rules(approve: [ids], reject: [ids])` with exactly the user's decisions. Approved rules apply from the next agent start. Rejected ones are deleted.
5. Report how many were approved, rejected and left staged.

Never approve a rule the user did not explicitly accept. If they are unsure, leave it staged. Do not spawn any agent.

---
description: Add new facts to your ground-truth CV from a GitHub repo, a project website, or a local file (a CV section, a cover letter)
argument-hint: "<github-url | website-url | /path/to/file>"
---

The input is `$ARGUMENTS`. If it is empty, ask: "What would you like to add? A URL (GitHub repo, project website) or a local file path."

1. **Check the input:** a URL must parse; a file path must exist. Otherwise ask the user to clarify.
2. **Spawn `sourceUpdater`** with `input`.
3. It returns what it found, the changes it proposes, and its questions. **Save nothing until every question is answered**; relay the answers to the same agent and let it update its proposal.
4. **Show the proposal** clearly: which sections change, what is added, what already exists and is skipped.
5. **Ask:** "Save these changes? (yes / no / edit)"
   - yes: send the same sourceUpdater the approval; it saves the changes.
   - no: discard.
   - edit: take the user's changes, show the proposal again, ask again.
6. Confirm what was saved. New facts apply from the next CV or letter written.

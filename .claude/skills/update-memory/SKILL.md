---
description: Update the shared project memory in docs/memory at the end of a session or task — current state, session log, open questions, lessons. Use after finishing a task, before ending a session, or when asked to save progress.
---

# Update memory

1. `docs/memory/progress.md`
   - Rewrite the **Current state** block (phase, last task done, next task, blockers) so a new session
     can start from it alone. Keep it under 15 lines.
   - Append a dated entry to the **Session log** (newest first): tasks done, key changes, verification
     results, follow-ups. 3–6 bullets.
2. `docs/memory/open-questions.md` — add questions that need the founders/partners; mark answered ones
   with the answer and date (and turn decisions into ADRs).
3. `docs/memory/lessons.md` — add anything that cost time: library quirks, API gotchas, Windows/Docker issues.
4. If a decision was made in this session without an ADR, tell the user and offer `/adr`.
5. If `CLAUDE.md` is now wrong (commands, repo map), fix it and keep it under 200 lines.

Only record facts that were verified in this session. Don't copy code into memory files.

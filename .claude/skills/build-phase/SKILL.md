---
description: Work through the next task (or a named task) in a build phase from docs/build, end to end with verification and memory updates. Use when the user runs /build-phase or asks to continue the build.
disable-model-invocation: true
argument-hint: <phase-number> [task-id]
---

# Build phase $0

Arguments: $ARGUMENTS (phase number, then an optional task ID such as P0-03).

1. **Orient.** Read `docs/memory/progress.md` and the phase file `docs/build/phase-$0-*.md`.
   Pick task `$1` if given; otherwise the first unchecked task whose dependencies are checked.
   If a task is marked **(human)**, don't do it: list what the human needs to do and pick the next task.
2. **Load context.** Read the ADRs and architecture docs the task links to. For Azure, Foundry or
   Agent Framework specifics, delegate to the `azure-researcher` subagent instead of relying on memory.
3. **Plan.** Enter plan mode. State: goal, acceptance criteria (copied from the task), files to
   create/change, tests to write, risks. If anything is ambiguous or conflicts with an ADR or a
   non-negotiable in CLAUDE.md, stop and ask the user.
4. **Build in small steps.** Tests alongside code. Keep each commit to one coherent change.
5. **Verify.** Run `/verify`. Fix until green. For tasks touching tenancy, connectors, agents or the
   executor, run the `security-reviewer` subagent and address findings. If the task changes the
   architecture, run `architect-reviewer` and write an ADR with `/adr`.
6. **Close out.** Tick the task in the phase file (`- [x]`) with a one-line note of what was built.
   Run `/update-memory`. Commit with message `<task-id>: <summary>`. Do not push.
7. **Report** to the user: what was built, how it was verified, anything left for a human, next task.

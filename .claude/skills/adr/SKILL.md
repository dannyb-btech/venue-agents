---
description: Create a new architecture decision record from the template, numbered and indexed. Use when a decision changes architecture, a vendor, a data flow, a non-negotiable, or reverses an earlier ADR.
argument-hint: <short decision title>
---

# New ADR: $ARGUMENTS

1. Find the highest number in `docs/adr/` and use the next one (4 digits).
2. Copy `docs/adr/0000-template.md` to `docs/adr/NNNN-<kebab-title>.md` and fill every section:
   context (with links to sources, preferring Microsoft Learn and vendor docs), the decision, options
   considered with why they lost, consequences (good and bad), and how we'd know to revisit.
3. Status is **Proposed** unless the user has explicitly agreed, then **Accepted** with today's date.
4. If it supersedes an ADR, set the old one's status to `Superseded by NNNN` and link both ways.
5. Add a row to the index in `docs/adr/README.md`.
6. Update any architecture doc or CLAUDE.md line the decision changes.

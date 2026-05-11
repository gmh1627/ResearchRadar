# ResearchRadar LLM Wiki Schema

This schema turns ResearchRadar from a stream reader into a persistent research wiki.
It follows the LLM Wiki pattern: raw sources remain immutable; the LLM maintains the
compiled wiki layer.

## Layers

- `items`, `notes`, `conversations`, `feedback`: raw and user-authored sources of truth.
- `wiki_pages`: generated markdown pages that synthesize those sources.
- `wiki_log`: append-only compilation log.

## Page Types

- `index`: content-oriented catalog of wiki pages.
- `overview`: current synthesis, open questions, and maintenance actions.
- `concept`: pages for methods, themes, entities, benchmarks, or recurring research ideas.
- `source`: pages for major channels such as arXiv, GitHub, AIHOT, Hacker News, or lab blogs.

## Maintenance Workflow

1. Read recent saved/deep-read/viewed items, notes, and question-answer records.
2. Update overview first.
3. Update or create concept pages for recurring tags and entities.
4. Update source pages for frequently used channels.
5. Regenerate index.
6. Append a log entry with a consistent timestamped event.

## Rules

- Cite source item ids and URLs where claims come from.
- Prefer updating an existing page over creating a near-duplicate page.
- Mark weak evidence, contradictions, and freshness issues explicitly.
- Good Q&A answers can be filed back into wiki pages.
- Do not modify raw sources while compiling the wiki.

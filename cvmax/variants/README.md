# System prompt variants

This folder holds the prompt variants among which the bandit (Thompson sampling, `cvmax/learning/bandit.py`)
chooses for each new review. A variant does not replace the prompt: its `text` is appended at the very
end of the review system prompt (`analysis_system`), after the rubric. An empty `text` means the
base prompt unchanged.

Schema of `analysis.json`:

    {"task": "analysis",
     "variants": [{"id": "v1-baseline", "active": true, "text": ""}, ...]}

- `id`: a stable identifier. Statistics (likes, accepted edits, outcome) are stored by id,
  so an id must not be renamed or reused: new text means a new id.
- `active`: whether the bandit includes this variant in its choice. The bandit can switch a losing variant off itself (`false`).
- `text`: instructions in English that change the way of reasoning, not the response schema. They
  cannot override the hard rules of the base prompt (invent nothing, placeholders in brackets, no cosmetic edits, `before` verbatim from the CV).

The weekly script `scripts/learn.py` may append new variants with the id `auto-<date>`; there is no need to delete variants,
a disabled variant keeps its history.

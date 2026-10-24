Here is the plan I came up with.

### Result
DONE — split the migration into three independent slices.

### Approach
Split by data model, API, and UI.

### Slices
1. Data model — `models.py`: add the new column. Depends on: none.

### Risks
- Slice 1: a missing default could break existing rows.

### Acceptance criteria
- Slice 1: migration applies cleanly.

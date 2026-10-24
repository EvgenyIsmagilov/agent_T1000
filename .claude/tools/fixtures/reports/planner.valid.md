### Result
DONE — split the migration into three independent slices.

### Approach
Split by data model, API, and UI so each slice can land and verify separately. Rejected a single big-bang slice: too large to review or roll back.

### Slices
1. Data model — `models.py`: add the new column. Depends on: none.
2. API — `views.py`: expose the new field. Depends on: slice 1.
3. UI — `templates/form.html`: render the new field. Depends on: slice 2.

### Risks
- Slice 1: a missing default could break existing rows.
- Slice 2: clients ignoring the new field is fine, additive only.

### Acceptance criteria
- Slice 1: migration applies cleanly, `pytest tests/test_models.py` passes.
- Slice 2: `pytest tests/test_views.py` passes.
- Slice 3: manual check that the field renders.

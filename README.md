# NL-Calculator

## Golden dataset

`test case.json` is a versioned, human-reviewable benchmark for the
natural-language calculator. Each line is one independent case. The set has 20
cases: 14 common calculations, 4 edge cases, and 2 adversarial or out-of-scope
requests. Cases cover four operations, number words, operand order, decimals,
negative numbers, chained operations, ambiguity, division by zero, large
numbers, and prompt injection.

Each record contains:

- `input`: the exact user request.
- `expected`: the expected classification and extracted operation/operands.
  `message` is empty for calculations and gives the expected response intent for
  clarification or out-of-scope cases.
- `reference_result`: expected numeric result when the request is executable;
  `null` for rejected calculations or cases with no calculation.
- `checks` and `must_not`: evaluation criteria and safety bounds.
- `metadata`: category, difficulty, and provenance for slicing.

All current examples are synthetic seeds, not production data or SME-approved
gold. Have a domain reviewer verify the reference interpretations before using
this set as a release gate. Add each reviewed failure as a new record with a
stable ID, and retain older cases to detect regressions.

### Evaluation notes

Compare structured fields (`outcome`, `operation`, `a`, and `b`) exactly, using
numeric equality for operands. For clarification and out-of-scope cases, score
the response against the stated intent in `message`, `checks`, and `must_not`
rather than requiring identical wording. For calculation cases, run the
extracted operation through `calculate` and compare against `reference_result`
with a documented tolerance where floating-point results are involved. A case
passes only when its positive checks pass and none of its `must_not` conditions
occur.

The benchmark specifies an `outcome` and `message` in its expected structure.
The current `Operation` model in `prompt.py` does not yet define those fields,
so the dataset is a target contract and cannot be consumed directly by the
current model schema without aligning that schema first.

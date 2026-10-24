### Result
DONE — the API's rate limit is documented at 100 req/min per key.

### Answer
The endpoint enforces 100 requests per minute per API key, returning 429 past that.

### Findings
- Limit is 100 req/min, effective since v4 (2025-11); older keys are exempt.
- Retry-After header is sent on 429; implication: clients must honor it.

### Recommended action
Add exponential backoff keyed on the `Retry-After` header.

### Sources
- [API reference](https://example.com/api/limits) — states the exact limit and version.

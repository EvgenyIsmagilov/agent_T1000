Quick summary before the formal report.

### Result
DONE — the API's rate limit is documented at 100 req/min per key.

### Answer
The endpoint enforces 100 requests per minute per API key.

### Findings
- Limit is 100 req/min, effective since v4.

### Recommended action
Add backoff.

### Sources
- [API reference](https://example.com/api/limits) — states the limit.

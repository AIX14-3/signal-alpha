# Collectors

Collectors fetch and normalize external data.

Collectors should not call LLMs. They should return raw or normalized evidence with source metadata, timestamps, URLs, and collection status.

## DART

`DartCollector` calls the OpenDART disclosure list API and returns `RawEvidence` rows with
`source="DART"`. It does not fetch or parse disclosure document bodies in the first MVP scope.

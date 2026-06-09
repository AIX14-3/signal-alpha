# Collectors

Collectors fetch and normalize external data.

Collectors should not call LLMs. They should return raw or normalized evidence with source metadata, timestamps, URLs, and collection status.

## DART

`DartCollector` calls the OpenDART disclosure list API and returns `RawEvidence` rows with
`source="DART"`. It reads `total_page` from the list response and requests every page so longer
date ranges are not truncated to page 1. When document fetching is enabled, it also calls
`document.xml` for each `receipt_no`, extracts text from the returned ZIP/XML files, and stores
that text in `RawEvidence.content` and `metadata.document_text`.

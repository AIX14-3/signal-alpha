# Collectors

Collectors fetch and normalize external data.

Collectors should not call LLMs. They should return raw or normalized evidence with source metadata, timestamps, URLs, and collection status.

## DART

`DartCollector` calls the OpenDART disclosure list API and returns `RawEvidence` rows with
`source="DART"`. It reads `total_page` from the list response and requests every page so longer
date ranges are not truncated to page 1. When document fetching is enabled, it also calls
`document.xml` for each `receipt_no`, extracts text from the returned ZIP/XML files, and stores
that text in `RawEvidence.content` and `metadata.document_text`.

## PRICE

`OhlcvReader` does not call any external API. The Kiwoom calls happen in
`services/price-collector`, which writes `ohlcv_data`; this collector reads those rows back
through `signal_alpha_data_access` and returns a single `RawEvidence` with `source="PRICE"`,
carrying the JSON-safe OHLCV/investor-flow rows in `metadata.rows` plus a `stale` marker when
the latest session is older than a week.

# intelligence.ingest — RSS ingest pipeline
#
# Fast metadata-only ingestion. Fetches feeds, dedupes by URL, scores
# relevance, stores briefs with content_status='pending'. Full content
# extraction is deferred to the content router (../content/router.py)
# called on demand from the API.

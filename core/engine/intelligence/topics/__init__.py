"""intelligence.topics — Auto-maintained topic index files.

The Karpathy pattern: each topic has a markdown wiki at
vault/knowledge/indexes/<slug>.md. The compilation engine (Pass 2) calls
update_index() whenever a new capture is written to keep the indexes fresh.

The overnight lint pass (Pass 3, Part 10) will rewrite orientation
paragraphs and open-questions lists using Sonnet.
"""

from .schema import TopicIndex, TopicEntry
from .builder import update_index, load_index, slugify

__all__ = ["TopicIndex", "TopicEntry", "update_index", "load_index", "slugify"]

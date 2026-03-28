from .youtube import YouTubeHandler
from .instagram import InstagramHandler
from .tiktok import TikTokHandler
from .twitter import TwitterHandler

# Registry: platform name → handler instance
HANDLERS = {
    "youtube": YouTubeHandler(),
    "instagram": InstagramHandler(),
    "tiktok": TikTokHandler(),
    "twitter": TwitterHandler(),
}


def get_handler(platform: str):
    """Get the handler for a given platform."""
    return HANDLERS.get(platform)

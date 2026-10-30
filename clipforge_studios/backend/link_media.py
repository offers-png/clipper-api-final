"""YouTube, TikTok and Instagram links for the Studio tools, with Classic's checks.

Only those hosts are accepted and they must resolve to public addresses
(url_media.check_transcribe_url); videos download at up to 1080p. The request
field is `link`, not `url`: the access layer treats `url` as a reference to the
caller's own Studio files.
"""
import os
from pathlib import Path
import shutil
from urllib.parse import urlparse
from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_BYTES = int(float(os.getenv('CLIPFORGE_LINK_MAX_MB', '1536')) * 1024 * 1024)
MAX_SECONDS = float(os.getenv('CLIPFORGE_LINK_MAX_MINUTES', '120')) * 60


class Link(BaseModel):
    link: str = Field(min_length=8, max_length=2048)


def name(link):
    """A readable source name, e.g. youtu.be/SJywU4UQryQ."""
    parsed = urlparse(link.strip())
    return ((parsed.hostname or 'link').removeprefix('www.') + parsed.path).rstrip('/')[:120]


def fetch(link, destination):
    """Download the link's video to destination; HTTPException with a user-facing reason on failure."""
    from url_media import MediaError, MediaTooLarge, UnsupportedSource, check_transcribe_url, download
    try:
        check_transcribe_url(link)
        downloaded = download(link, MAX_BYTES, MAX_SECONDS)
    except UnsupportedSource as error:
        raise HTTPException(400, str(error)) from None
    except MediaTooLarge as error:
        raise HTTPException(413, str(error)) from None
    except MediaError as error:
        raise HTTPException(422, str(error)) from None
    shutil.move(downloaded, destination)
    return Path(destination)

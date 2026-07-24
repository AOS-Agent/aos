"""Canada Post XML → dict mapper.

Canada Post's Track web service answers in XML
(``application/vnd.cpc.track-v2+xml``) while the tracking engine consumes
dicts via the pack's ``response_map``. This is the thin translation layer
the manifest comments refer to: the engine client calls
:func:`track_xml_to_dict` on the raw response body, then applies the pack's
``response_map`` to the returned dict.

Handles both response documents:

- ``<tracking-summary>``   (Get Tracking Summary — one pin-summary per PIN)
- ``<tracking-detail>``    (Get Tracking Details — significant-events list)

and raises :class:`CanadaPostError` on CPC ``<messages>`` error payloads
(e.g. code 004 "No Pin History", AA002 auth failures).

Output shape (matches carriers/canadapost/manifest.yaml response_map)::

    {
      "pin": "0073938000549297",
      "service": "Xpresspost",
      "eta": "2011-04-08",            # expected-delivery-date / date-expected
      "delivered_on": "2011-04-05",   # actual-delivery-date, None if not delivered
      "events": [
        {"timestamp": "2011-04-05T09:59:24", "timezone": "EST",
         "code": "1476", "description": "Delivered",
         "location": "TORONTO", "province": "ON", "signatory": "T SMITH"},
        ...
      ],
    }

Python 3.9 compatible, stdlib only. ElementTree does not resolve external
entities, so no XXE surface; payloads still come only from the pinned CPC
endpoint over TLS.
"""

from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

__all__ = ["CanadaPostError", "track_xml_to_dict"]


class CanadaPostError(ValueError):
    """Raised when CPC returns a ``<messages>`` error payload or bad XML."""

    def __init__(self, code: str, description: str) -> None:
        self.code = code
        super().__init__("Canada Post error %s: %s" % (code, description))


def _strip_ns(tag: str) -> str:
    """Drop the ``{namespace}`` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def _text(parent: ElementTree.Element, name: str) -> Optional[str]:
    """Text of the first direct child named *name* (ns-insensitive), or None."""
    for child in parent:
        if _strip_ns(child.tag) == name:
            value = (child.text or "").strip()
            return value or None
    return None


def _children(parent: ElementTree.Element, name: str) -> List[ElementTree.Element]:
    return [c for c in parent if _strip_ns(c.tag) == name]


def _first_descendant(root: ElementTree.Element, name: str) -> Optional[ElementTree.Element]:
    for el in root.iter():
        if _strip_ns(el.tag) == name:
            return el
    return None


def _parse_occurrence(occ: ElementTree.Element) -> Dict[str, Any]:
    """One <occurrence> (detail) → normalized event dict."""
    return {
        "timestamp": _text(occ, "event-date-time"),
        "timezone": _text(occ, "event-time-zone"),
        "code": _text(occ, "event-identifier"),
        "description": _text(occ, "event-description"),
        "location": _text(occ, "event-site"),
        "province": _text(occ, "event-province"),
        "signatory": _text(occ, "signatory-name"),
    }


def _parse_summary_event(pin_summary: ElementTree.Element) -> Dict[str, Any]:
    """The single current-status event embedded in a <pin-summary>."""
    return {
        "timestamp": _text(pin_summary, "event-date-time"),
        "timezone": None,
        "code": _text(pin_summary, "event-type"),
        "description": _text(pin_summary, "event-description"),
        "location": _text(pin_summary, "event-location"),
        "province": None,
        "signatory": _text(pin_summary, "signatory-name"),
    }


def track_xml_to_dict(xml_text: str) -> Dict[str, Any]:
    """Map a CPC Track XML response body to the response_map dict shape.

    Raises CanadaPostError on CPC error payloads and on unparseable XML.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise CanadaPostError("PARSE", "invalid XML: %s" % exc)

    root_name = _strip_ns(root.tag)

    if root_name == "messages":
        message = _first_descendant(root, "message")
        code = _text(message, "code") if message is not None else None
        description = _text(message, "description") if message is not None else None
        raise CanadaPostError(code or "UNKNOWN", description or "unknown error")

    if root_name == "tracking-detail":
        events_parent = _first_descendant(root, "significant-events")
        occurrences = _children(events_parent, "occurrence") if events_parent is not None else []
        return {
            "pin": _text(root, "pin"),
            "service": _text(root, "service-name"),
            "eta": _text(root, "expected-delivery-date") or _text(root, "date-expected"),
            "delivered_on": _text(root, "actual-delivery-date"),
            "events": [_parse_occurrence(occ) for occ in occurrences],
        }

    if root_name == "tracking-summary":
        pin_summary = _first_descendant(root, "pin-summary")
        if pin_summary is None:
            raise CanadaPostError("EMPTY", "tracking-summary without pin-summary")
        return {
            "pin": _text(pin_summary, "pin"),
            "service": _text(pin_summary, "service-name"),
            "eta": _text(pin_summary, "expected-delivery-date"),
            "delivered_on": _text(pin_summary, "actual-delivery-date"),
            "events": [_parse_summary_event(pin_summary)],
        }

    raise CanadaPostError("UNEXPECTED", "unexpected root element <%s>" % root_name)

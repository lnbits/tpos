import json

from loguru import logger


def from_csv(value: str | None, separator: str = ",") -> list[str]:
    if not value:
        return []
    parts = [part.strip() for part in value.split(separator)]
    return [part for part in parts if part]


def serialize_inventory_tags(tags: list[str] | str | None) -> str | None:
    if isinstance(tags, list):
        return ",".join([tag for tag in tags if tag])
    return tags


def inventory_tags_to_list(raw_tags: str | list[str] | None) -> list[str]:
    if raw_tags is None:
        return []
    if isinstance(raw_tags, list):
        return [tag.strip() for tag in raw_tags if tag and tag.strip()]
    return [tag.strip() for tag in raw_tags.split(",") if tag and tag.strip()]


def inventory_tags_to_string(raw_tags: str | list[str] | None) -> str | None:
    if raw_tags is None:
        return None
    if isinstance(raw_tags, str):
        return raw_tags
    return ",".join([tag for tag in raw_tags if tag])


def first_image(images: str | list[str] | None) -> str | None:
    if not images:
        return None
    if isinstance(images, list):
        return normalize_image(images[0]) if images else None
    raw = str(images).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            return normalize_image(parsed[0])
    except Exception as exc:
        logger.exception(f"Exception occurred while parsing image JSON: {exc}")

    if "|||" in raw:
        return normalize_image(raw.split("|||")[0])

    if "," in raw:
        return normalize_image(raw.split(",")[0])
    return normalize_image(raw)


def normalize_image(val: str | None) -> str | None:
    if not val:
        return None
    val = str(val).strip()
    if not val:
        return None
    if val.startswith("http") or val.startswith("/api/") or val.startswith("data:"):
        return val
    return f"/api/v1/assets/{val}/thumbnail"

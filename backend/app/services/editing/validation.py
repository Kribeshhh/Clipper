from app.core.errors import StudioError
from app.services.media import probe


def validate_export(target, duration, vertical, expect_audio):
    result = probe(target)
    tolerance = max(0.12, 2 / result["fps"])
    if abs(result["duration"] - duration) > tolerance:
        raise StudioError(422, "output_validation_failed")
    if vertical and (result["width"], result["height"]) != (720, 1280):
        raise StudioError(422, "output_validation_failed")
    if expect_audio is not None and result["has_audio"] != expect_audio:
        raise StudioError(422, "output_validation_failed")
    return result

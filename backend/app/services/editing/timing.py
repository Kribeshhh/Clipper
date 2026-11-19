from app.core.errors import StudioError


def validate_interval(start, end, duration, fps):
    if (
        not (0 <= start < end <= duration)
        or end - start < min(1 / fps, duration) - 1e-8
    ):
        raise StudioError(422, "invalid_range")


def seek_args(start):
    return ["-ss", str(start)]


def duration_args(start, end):
    return ["-t", str(end - start)]

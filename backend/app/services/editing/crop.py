from statistics import median


def suggested_focus(observations, start, end):
    points = [p for p in observations or [] if start <= p[0] < end]
    if len(points) < 4 or len(points) < (end - start) * 0.6:
        return None
    x, y = (median(p[i] for p in points) for i in (1, 2))
    stable = sum(abs(px - x) < 0.13 and abs(py - y) < 0.15 for _, px, py in points)
    if stable / len(points) < 0.8:
        return None
    return {"x": x, "y": y}


def crop_filter(position, frames=None):
    if not 0 <= position <= 1:
        raise ValueError("Invalid crop offset")
    expression = f"{position:.6f}"
    for frame in reversed((frames or [])[:-1]):
        expression = (
            f"if(lt(t,{frame['end']:.6f}),{frame['position']:.6f},{expression})"
        )
    # dar and sar are evaluated after FFmpeg's display rotation.
    return (
        "crop=w='max(1,min(iw,ih*9/16/sar))':h='max(1,min(ih,iw*sar*16/9))'"
        f":x='(iw-ow)*({expression})':y='(ih-oh)*({expression})':exact=1"
    )


def framing_plan(observations, samples, spans, width, height):
    """Use stable measured focus within scenes; change position only at scene cuts."""
    frames = []
    offset = 0.0
    for start, end in spans:
        cuts = [start]
        for i in range(int(start * 2) + 1, min(len(samples), int(end * 2))):
            row = samples[i]
            point = i / 2
            if row and row[1] >= 0.42 and point - cuts[-1] >= 2 and end - point >= 2:
                cuts.append(point)
                if len(cuts) == 8:
                    break
        for left, right in zip(cuts, cuts[1:] + [end], strict=True):
            focus = suggested_focus(observations, left, right)
            position = focus["x" if width >= height else "y"] if focus else 0.5
            frames.append(
                {
                    "start": offset + left - start,
                    "end": offset + right - start,
                    "position": position,
                    "basis": "localized_motion" if focus else "center",
                }
            )
        offset += end - start
    return frames

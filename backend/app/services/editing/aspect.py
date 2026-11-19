from app.services.editing.crop import crop_filter


def video_filter(edit):
    if not edit:
        return "pad=ceil(iw/2)*2:ceil(ih/2)*2"
    # Fit the integer-pixel crop inside a square-pixel portrait canvas.
    scale = (
        "scale=w='min(720,max(2,2*round(1280*dar/2)))'"
        ":h='min(1280,max(2,2*round(720/dar/2)))'"
    )
    filters = [
        crop_filter(
            edit["framing"][-1]["position"]
            if edit.get("framing")
            else edit["position"],
            edit.get("framing"),
        ),
        scale,
        "setsar=1",
        "pad=720:1280:(ow-iw)/2:(oh-ih)/2",
    ]
    if edit.get("accent") is not None:
        fps = edit["fps"]
        accent = edit["accent"]
        if not 0 < fps <= 60 or not 0 <= accent <= 180:
            raise ValueError("Invalid zoom timing")
        filters.extend(
            [
                f"fps={fps:.6f}",
                f"zoompan=z='1+0.035*max(0,1-abs(on/{fps:.6f}-{accent:.6f})/1.2)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=720x1280:fps={fps:.6f}",
            ]
        )
    if edit["cues"]:
        filters.append("ass=captions.ass")
    return ",".join(filters)

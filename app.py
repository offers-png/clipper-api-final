def parse_time(t: str) -> float:
    """Convert flexible time formats into seconds (supports 5, 00:05, 0:05, 00:00:05, etc.)"""
    try:
        if not t:
            raise ValueError("Empty time string")

        t = str(t).strip().replace("string", "").replace("'", "").replace('"', "")

        # If user gives just seconds
        if ":" not in t:
            return float(t)

        parts = [float(p) for p in t.split(":") if p.strip() != ""]

        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {t}")

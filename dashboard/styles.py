def severity_badge(severity: int):
    if severity >= 8:
        return f"🟥 **Critical ({severity})**"
    elif severity >= 5:
        return f"🟧 **High ({severity})**"
    return f"🟩 **Low ({severity})**"

def progress_bar(step=1):

    bars = [
        "░░░░░░░░░░",
        "██░░░░░░░░",
        "████░░░░░░",
        "██████░░░░",
        "████████░░",
        "██████████"
    ]

    if step < 0:
        step = 0

    if step >= len(bars):
        step = len(bars) - 1

    return bars[step]

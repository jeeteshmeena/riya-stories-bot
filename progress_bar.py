def progress_bar(step):

    bars = [
        "🔍 Searching.",
        "🔍 Searching..",
        "🔍 Searching...",
        "🔍 Searching...."
    ]

    return bars[step % len(bars)]

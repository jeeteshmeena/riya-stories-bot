from telegram import InlineQueryResultArticle, InputTextMessageContent
from rapidfuzz import fuzz
from database import load_db


RESULTS_PER_PAGE = 10


def search_inline(query, offset):

    db = load_db()

    offset = int(offset) if offset else 0

    results = []

    matches = []

    for name, data in db.items():

        score = fuzz.ratio(query.lower(), name)

        if score > 60:

            matches.append(data)

    matches.sort(key=lambda x: x["text"])

    sliced = matches[offset: offset + RESULTS_PER_PAGE]

    for story in sliced:

        results.append(

            InlineQueryResultArticle(

                id=story["name"],

                title=story["text"],

                description="Open story",

                input_message_content=InputTextMessageContent(

                    f"{story['text']}\n{story['link']}"

                )

            )

        )

    next_offset = offset + RESULTS_PER_PAGE

    if next_offset >= len(matches):

        next_offset = ""

    return results, str(next_offset)

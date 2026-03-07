from database import search_story

def search(query):

    result = search_story(query.lower())

    if result:
        return result

    return None

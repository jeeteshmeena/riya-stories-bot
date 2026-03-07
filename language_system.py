
def get_language_reply(lang,key):
    data={
        "en":{"not_found":"Story not found"},
        "hi":{"not_found":"कहानी नहीं मिली"},
        "hinglish":{"not_found":"Story nahi mili"}
    }
    return data.get(lang,{}).get(key,"")

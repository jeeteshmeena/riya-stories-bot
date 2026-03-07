FORMATS = [
    ["Name","Story Type"],
    ["Story","Platform"],
    ["Title","Genre"]
]

def check_format(text):

    for f in FORMATS:
        ok = True

        for field in f:
            if field.lower() not in text.lower():
                ok = False

        if ok:
            return True

    return False

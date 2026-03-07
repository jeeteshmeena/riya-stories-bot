
def is_valid_query(text):
    ignore=["hi","hello","thanks","ok","good morning","good night"]
    if len(text)<3:
        return False
    if text.lower() in ignore:
        return False
    return True

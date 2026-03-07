import re
from format_manager import check_format

def parse_story(msg):

    if not msg.text:
        return None

    text = msg.text

    if not check_format(text):
        return None

    name = re.findall(r"Name\s*[:-]\s*(.*)",text)

    links = re.findall(r"https://t\.me/\S+",text)

    if not name or not links:
        return None

    return {
        "name": name[0],
        "link": links[0]
    }

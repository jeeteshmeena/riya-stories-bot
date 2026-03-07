import re

def extract_story(text):

    name=None
    story_type=None
    link=None

    n=re.search(r"Name\s*[:-]\s*(.+)",text,re.I)
    t=re.search(r"Story Type\s*[:-]\s*(.+)",text,re.I)
    l=re.search(r"https?://\S+",text)

    if n:
        name=n.group(1).strip()

    if t:
        story_type=t.group(1).strip()

    if l:
        link=l.group(0)

    if name:
        return {
            "name":name,
            "type":story_type or "Unknown",
            "link":link
        }

    return None

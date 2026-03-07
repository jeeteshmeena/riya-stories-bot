import json

FILE = "channels.json"

def load_channels():
    try:
        with open(FILE) as f:
            return json.load(f)
    except:
        return []

def add_channel(cid):

    channels = load_channels()

    if cid not in channels:
        channels.append(cid)

    with open(FILE,"w") as f:
        json.dump(channels,f)

import re
with open('stories_bot.py', encoding='utf-8') as f:
    c = f.read()
m = re.search(r'def storylist_cmd.*?def ', c, re.DOTALL)
if m:
    print(m.group(0))
else:
    print("Not found")

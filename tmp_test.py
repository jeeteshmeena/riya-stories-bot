import json

voting_queue = []
active_polls = {}
request_db = {}
VOTING_SIZE_FOR_POLL = 3

def request_story_mock(story_raw, chat_id, user_id):
    global voting_queue, request_db
    chat_id = str(chat_id)
    story = story_raw.strip().lower()
    
    # simulate request_db tracking
    if story not in request_db:
        request_db[story] = {}
    if chat_id not in request_db[story]:
        request_db[story][chat_id] = set()

    if user_id in request_db[story][chat_id]:
        print("spam!")
        return
        
    request_db[story][chat_id].add(user_id)
    count = sum(len(uids) for uids in request_db[story].values())
    
    print(f"[{story}] DB Count: {count}")
    
    # Voting Queue Integration
    in_queue = any(q["name"] == story for q in voting_queue)
    in_polls = any(story in p["options"] for p in active_polls.values())

    if not in_queue and not in_polls:
        voting_queue.append({
            "name": story,
            "requesters": {chat_id: [user_id]}
        })
    elif in_queue:
        for q in voting_queue:
            if q["name"] == story:
                chat_reqs = q.get("requesters", {})
                chat_reqs.setdefault(chat_id, [])
                if user_id not in chat_reqs[chat_id]:
                    chat_reqs[chat_id].append(user_id)
                q["requesters"] = chat_reqs
                break
                
    queue_len = len(voting_queue)
    print(f"-> Voting Queue length: {queue_len}/3")
    
    if queue_len >= VOTING_SIZE_FOR_POLL:
        print("TRIGGER POLL")
        # simulate failure
        to_poll = voting_queue[:VOTING_SIZE_FOR_POLL]
        voting_queue = voting_queue[VOTING_SIZE_FOR_POLL:]
        # oops, failed
        voting_queue = to_poll + voting_queue

print("Simulation:")
request_story_mock("Story A", 111, 1)
request_story_mock("Story B", 222, 2)
request_story_mock("Story C", 333, 3)
request_story_mock('Story D', 444, 4)

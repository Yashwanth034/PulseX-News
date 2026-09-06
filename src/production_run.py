import json
import os
from pathlib import Path
from src.production_controller import controller
from src.x_publisher import XPublisher, XPublisherError
from src.review import load

ROOT=Path(__file__).resolve().parents[1]
QUEUE=ROOT/"data/queue.json"
LOG=ROOT/"data/publish_log.json"

def main():
    decision=controller()
    if not decision["allowed"]:
        print("LIVE PUBLISHING BLOCKED.")
        return

    queue=json.loads(QUEUE.read_text()) if QUEUE.exists() else {"stories":[]}
    method=os.getenv("X_POST_METHOD","web").lower()
    if method=="api":
        publisher=XPublisher()
    else:
        from src.x_web_publisher import XWebPublisher
        publisher=XWebPublisher()
    reviews=load()["reviews"]
    require_review = os.getenv("X_REQUIRE_HUMAN_REVIEW","true").lower()=="true"
    remaining_capacity = max(0, int(decision.get("publish_capacity", 0)))
    results=[]
    publish_errors=[]

    for item in queue.get("stories",[]):
        review=reviews.get(item.get("id"),{})
        if require_review and review.get("decision")!="APPROVE":
            results.append({"title":item.get("title"),"blocked":"human approval required","decision":review.get("decision","PENDING")})
            continue

        fmt = item.get("format")
        required_posts = 1 if fmt == "single" else len([
            text for text in item.get("thread", []) if (text or "").strip()
        ])

        if required_posts <= 0:
            error = "Cannot publish empty post/thread"
            results.append({"title":item.get("title"),"error":error})
            publish_errors.append(error)
            continue

        if required_posts > remaining_capacity:
            results.append({
                "title":item.get("title"),
                "blocked":"current publishing capacity exhausted",
                "required_posts":required_posts,
                "remaining_capacity":remaining_capacity,
            })
            continue

        try:
            posted=publisher.publish(item)
            results.append({"title":item.get("title"),"format":fmt,"result":posted})
            remaining_capacity -= required_posts
        except XPublisherError as exc:
            error = str(exc)
            results.append({"title":item.get("title"),"error":error})
            publish_errors.append(error)
            # Stop after the first real publishing failure. Retrying other
            # queued stories in the same run can repeat a broken login/session
            # state or produce confusing partial failures.
            break

    LOG.write_text(json.dumps(results,indent=2,ensure_ascii=False))
    print(json.dumps(results,indent=2,ensure_ascii=False))

    if publish_errors:
        raise SystemExit(1)

if __name__=="__main__":main()

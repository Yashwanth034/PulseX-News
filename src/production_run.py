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
    results=[]
    for item in queue.get("stories",[]):
        review=reviews.get(item.get("id"),{})
        if require_review and review.get("decision")!="APPROVE":
            results.append({"title":item.get("title"),"blocked":"human approval required","decision":review.get("decision","PENDING")})
            continue

        try:
            posted=publisher.publish(item)
            results.append({"title":item.get("title"),"format":item.get("format"),"result":posted})
        except XPublisherError as exc:
            results.append({"title":item.get("title"),"error":str(exc)})

    LOG.write_text(json.dumps(results,indent=2,ensure_ascii=False))
    print(json.dumps(results,indent=2,ensure_ascii=False))

if __name__=="__main__":main()

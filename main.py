import stomp
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from typing import Dict
import requests
import os
import json
import asyncio
import traceback

app = FastAPI()


# Store connected users temporarily in-memory (or use Redis for distributed state)
connected_users: Dict[str, WebSocket] = {}
SESSION_MANAGER_URL = "http://fastapi-session-manager-service.default.svc.cluster.local:8085";
# Get Kubernetes environment variables for the pod
POD_NAME = os.getenv("POD_NAME", "local_dev")
POD_NAMESPACE = os.getenv("POD_NAMESPACE", "default")
# Kubernetes service URL for chat service
SERVER_URL = f"http://{POD_NAME}.fastapi-chat-service.{POD_NAMESPACE}.svc.cluster.local"
QUEUE_NAME = f"/queue/{POD_NAME}"
activemq_host = os.getenv("ACTIVEMQ_HOST", "localhost")

# Save reference to the main event loop
main_loop = asyncio.get_event_loop()

# ActiveMQ Listener
class MQListener(stomp.ConnectionListener):
    def on_message(self, frame):
        asyncio.run_coroutine_threadsafe(
            dispatch_message(frame.body),
            main_loop
        )

# Dispatch message to the WebSocket
async def dispatch_message(raw: str):
    msg = json.loads(raw)
    to_user = msg["to"]
    print(f"received the following message from the queue {msg}")
    if to_user in connected_users:
        await connected_users[to_user].send_json({
            "from": msg["from"],
            "message": msg["message"]
        })
        return True
    else:
        print(f"cannot find the user in active websockets {to_user}")
        return False

# ActiveMQ Connection Setup
# ActiveMQ Connection Setup (updated to use localhost)
conn = stomp.Connection([(activemq_host, 61616)],heartbeats=(10000, 10000))  # ActiveMQ running on localhost
#conn = stomp.Connection([(activemq_host, 61616)], heartbeats=(10000, 10000))
conn.set_listener("", MQListener())
conn.connect("artemis", "artemis", wait=True)
conn.subscribe(destination=QUEUE_NAME, id=1, ack='client-individual')



def register_user(username: str, websocket: WebSocket):
 # Replace this with the actual server URL for the chat service
    try:
        # Send a request to session manager to register the user
        response = requests.post(f"{SESSION_MANAGER_URL}/register_user", json={"username": username, "queue_name": QUEUE_NAME})
        print(response)

        if response.status_code == 200:
            print(f"✅ Registered user: {username} in session manager.")
        else:
            print(f"❌ Failed to register user: {username} in session manager.")
    except requests.exceptions.RequestException as e:
        print(f"Error contacting session manager: {str(e)}")

    # Store the user in local memory as well
    connected_users[username] = websocket
    print(f"✅ Registered user locally: {username}")


async def handle_messages(username: str, websocket: WebSocket):
    while True:
        data = await websocket.receive_json()
        to_user = data.get("to")
        message = data.get("message")
        print(f"data received from {username}")

        if not to_user or not message:
            await websocket.send_json({"error": "Invalid message format"})
            continue

        if to_user in connected_users:
            try:
                # Send the message to the local user if they are connected
                await connected_users[to_user].send_json({
                    "from": username,
                    "message": message
                })
                await websocket.send_json({
                    "system": f"Sent message to {to_user}"
                })
            except Exception as e:
                await websocket.send_json({
                    "error": f"Failed to send to {to_user}. Error: {str(e)}"
                })
        else:
            try:
                response = requests.get(f"{SESSION_MANAGER_URL}/get_server/{to_user}")
                print(response)
                if response.status_code == 200:
                    to_user_queue = response.json().get("queue_name")
                    print(f"sending data to following queue {to_user_queue}")
                    reconnect_activemq();
                    # Push the message to ActiveMQ queue
                    message_data = json.dumps({
                        "from": username,
                        "message": message,
                        "to": to_user
                    })
                    conn.send(to_user_queue,  message_data,persistent='true')  # Send to the ActiveMQ queue
                    print(f"✅ Message sent to ActiveMQ queue: {to_user_queue}")

                else:
                    print("unable to find recipient server address")
            except Exception as e:
                print(f"Error while processing: {str(e)}")
                traceback.print_exc()

def reconnect_activemq():
    if not conn.is_connected():
        print("🔌 STOMP connection lost. Attempting to reconnect...")
        try:
            conn.connect("artemis", "artemis", wait=True)
            conn.subscribe(destination=QUEUE_NAME, id=1, ack='client-individual')
            print("✅ Reconnected and re-subscribed.")
        except Exception as e:
            print(f"❌ Failed to reconnect: {e}")


@app.post("/ws/post/{username}")
async def relay_message(username: str, data: dict):
    # Data should include the message from the other server
    message = data.get("message")
    to_user = data.get("to")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    if to_user in connected_users:
        await connected_users[to_user].send_json({
            "from": username,
            "message": message
        })
        return {"status": "Message relayed to user", "username": to_user}
    else:
        print(f"Error sending message to {to_user}")
        return {"status": "Not sent", "to_user": to_user}

@app.websocket("/ws/chat/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    register_user(username, websocket)

    try:
        await handle_messages(username, websocket)
    except WebSocketDisconnect:
        print(f"❌ {username} disconnected")
        connected_users.pop(username, None)
        await websocket.close()


@app.get("/")
def read_root():
    return {"status": "UP"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

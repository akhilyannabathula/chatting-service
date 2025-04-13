import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from typing import Dict
import requests
import os

app = FastAPI()


# Store connected users temporarily in-memory (or use Redis for distributed state)
connected_users: Dict[str, WebSocket] = {}
SESSION_MANAGER_URL = "http://session-manager-service.default.svc.cluster.local:8080";
# Get Kubernetes environment variables for the pod
POD_NAME = os.getenv("POD_NAME", "unknown_pod")
POD_NAMESPACE = os.getenv("POD_NAMESPACE", "default")


# Kubernetes service URL for chat service
SERVER_URL = f"{POD_NAME}.fastapi-chat-service.{POD_NAMESPACE}.svc.cluster.local"

def register_user(username: str, websocket: WebSocket):
 # Replace this with the actual server URL for the chat service
    try:
        # Send a request to session manager to register the user
        response = requests.post(SESSION_MANAGER_URL, json={"username": username, "server_url": SERVER_URL})
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
                response = requests.get(f"http://session-manager-service:8000/get_server/{to_user}")
                if response.status_code == 200:
                    to_user_server = response.json().get("server_url")
                    relay_response = requests.post(
                        f"{to_user_server}/ws/chat/{to_user}",
                        json={"from": username, "message": message}
                    )
                    print(relay_response)
                else:
                    print("unable to find recipient server address")
            except requests.exceptions.RequestException as e:
                print(f"Error contacting session manager: {str(e)}")





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


@app.get("/")
def read_root():
    return {"status": "UP"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

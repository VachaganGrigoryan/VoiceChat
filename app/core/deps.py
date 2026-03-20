import socketio
from starlette.requests import Request


# Define the dependency function
def get_sio(request: Request) -> socketio.AsyncServer:
    return request.app.state.sio
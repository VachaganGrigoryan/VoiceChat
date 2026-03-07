import { io } from "socket.io-client";

const JWT_TOKEN = 'Enter JWT token';

const socket = io("http://localhost:8000", {
  auth: { token: JWT_TOKEN },
});

socket.on("receive_voice_message", (message) => {
  // show message
  console.log("new voice message", message);

  // immediately ack delivered
  socket.emit("voice_message_delivered", { message_id: message.id });

  console.log(`Voice message ID ${message.id} Delivered`)

  // when user plays it (or opens chat), mark read:
  // socket.emit("voice_message_read", { message_id: message.id });
});

socket.on("presence_update", ({ user_id, online }) => {
  console.log("presence changed", user_id, online);
});
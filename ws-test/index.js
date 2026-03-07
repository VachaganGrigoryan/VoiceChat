import { io } from "socket.io-client";

const JWT_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2OWE5NDU5ZWRiYTA4YjA2MjQ4ZDMyNDciLCJpYXQiOjE3NzI4MTc1MjUsImV4cCI6MTc3MjgyMTEyNX0.8kC5HDyBEk0lh9vn_o8sq8AkPRHOMjuPByA6UGK884o';

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
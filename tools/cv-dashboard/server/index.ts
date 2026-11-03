import { API_PORT, DB_PATH, HOST, REPO_ROOT } from "./config.js";
import { createApp } from "./http.js";

const { server } = createApp();
server.listen(API_PORT, HOST, () => {
  const ui = process.env.NODE_ENV === "production" ? `http://localhost:${API_PORT}` : "http://localhost:5173";
  console.log(`cv-dashboard API on http://${HOST}:${API_PORT} · repo ${REPO_ROOT} · db ${DB_PATH} (read-only)`);
  console.log(`open ${ui}`);
});

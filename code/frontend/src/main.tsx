import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

// Acces web (pseudo + mot de passe) : le navigateur presente le token
// obtenu au login sur tous les appels /api/*. Une reponse 401 declenche
// l'evenement "neo-401" -> App affiche la porte de login.
const fetchOriginel = window.fetch.bind(window);
window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : (input as Request).url;
  const token = localStorage.getItem("neo.token");
  let init2 = init;
  if (url.includes("/api/") && token) {
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined)
    );
    if (!headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    init2 = { ...(init ?? {}), headers };
  }
  return fetchOriginel(input, init2).then((rep) => {
    if (rep.status === 401 && url.includes("/api/")) {
      window.dispatchEvent(new CustomEvent("neo-401"));
    }
    return rep;
  });
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);

import { useCallback, useEffect, useRef, useState } from "react";
import ChatView from "./components/ChatView";
import CollabView from "./components/CollabView";
import DocumentsView from "./components/DocumentsView";
import FineTuneView from "./components/FineTuneView";
import HomeView from "./components/HomeView";
import ModelsView from "./components/ModelsView";
import SettingsView from "./components/SettingsView";
import Sidebar from "./components/Sidebar";
import type { Conversation, ModelEntry, SystemInfo } from "./lib/api";
import { getJSON, sendJSON } from "./lib/api";

type Vue =
  | "accueil"
  | "chat"
  | "models"
  | "documents"
  | "collab"
  | "finetune"
  | "settings";

interface Toast {
  id: number;
  msg: string;
  kind: "info" | "error" | "success";
}

export default function App() {
  const [view, setView] = useState<Vue>("accueil");
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedConv, setSelectedConv] = useState<number | null>(null);
  const [selectedModel, setSelectedModel] = useState(
    () => localStorage.getItem("localai.model") ?? ""
  );
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [prefill, setPrefill] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);

  const toast = useCallback((msg: string, kind: Toast["kind"] = "info") => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      const data = await getJSON<{ models: ModelEntry[] }>("/api/models");
      setModels(data.models);
    } catch {
      toast("Impossible de joindre le serveur local", "error");
    }
  }, [toast]);

  const refreshConversations = useCallback(async () => {
    try {
      const data = await getJSON<{ conversations: Conversation[] }>("/api/conversations");
      setConversations(data.conversations);
    } catch {
      /* silent */
    }
  }, []);

  const refreshSystem = useCallback(() => {
    getJSON<SystemInfo>("/api/system/info")
      .then(setSystemInfo)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    void refreshModels();
    void refreshConversations();
    refreshSystem();
    const timer = setInterval(refreshSystem, 4000);
    return () => clearInterval(timer);
  }, [refreshModels, refreshConversations, refreshSystem]);

  useEffect(() => {
    localStorage.setItem("localai.model", selectedModel);
  }, [selectedModel]);

  useEffect(() => {
    if (!selectedModel && models.length > 0) {
      const firstInstalled = models.find((m) => m.installed && !m.tag.startsWith("nomic"));
      if (firstInstalled) setSelectedModel(firstInstalled.tag);
    }
  }, [models, selectedModel]);

  const newChat = useCallback((prefillMsg?: string) => {
    setSelectedConv(null);
    setPrefill(prefillMsg ?? null);
    setView("chat");
  }, []);

  const deleteConv = useCallback(
    async (id: number) => {
      try {
        await sendJSON(`/api/conversations/${id}`, "DELETE");
        setConversations((c) => c.filter((x) => x.id !== id));
        if (selectedConv === id) setSelectedConv(null);
        void refreshConversations();
      } catch {
        toast("Suppression impossible", "error");
      }
    },
    [selectedConv, refreshConversations, toast]
  );

  return (
    <div className="app">
      <Sidebar
        view={view}
        onView={setView}
        conversations={conversations}
        selectedConv={selectedConv}
        onSelectConv={(id) => {
          setSelectedConv(id);
          setView("chat");
        }}
        onDeleteConv={(id) => void deleteConv(id)}
        onNewChat={() => newChat()}
        systemInfo={systemInfo}
      />
      <main className="main">
        {view === "accueil" && (
          <HomeView
            onView={setView}
            onNewChat={newChat}
            systemInfo={systemInfo}
            models={models}
          />
        )}
        {view === "chat" && (
          <ChatView
            models={models}
            selectedModel={selectedModel}
            onSelectModel={setSelectedModel}
            conversationId={selectedConv}
            initialPrompt={prefill}
            onConversationCreated={(id) => {
              setSelectedConv(id);
              void refreshConversations();
            }}
            onAfterResponse={() => void refreshConversations()}
            toast={toast}
          />
        )}
        {view === "models" && (
          <ModelsView models={models} refreshModels={refreshModels} toast={toast} />
        )}
        {view === "documents" && <DocumentsView toast={toast} />}
        {view === "collab" && <CollabView models={models} toast={toast} />}
        {view === "finetune" && <FineTuneView toast={toast} />}
        {view === "settings" && (
          <SettingsView systemInfo={systemInfo} refreshSystem={refreshSystem} toast={toast} />
        )}
      </main>
      <div className="toast-zone">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.msg}
          </div>
        ))}
      </div>
    </div>
  );
}

import type { Conversation } from "../lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  conversations: Conversation[];
  selectedConv: number | null;
  onSelectConv: (id: number) => void;
  onDeleteConv: (id: number) => void;
  onNewChat: () => void;
}

/**
 * Panneau lateral masquable : l'historique des conversations stockées
 * (consigne : il quitte la barre laterale, remplace par l'encart tokens).
 */
export default function HistoryPanel({
  open,
  onClose,
  conversations,
  selectedConv,
  onSelectConv,
  onDeleteConv,
  onNewChat,
}: Props) {
  return (
    <aside className={`history-panel ${open ? "open" : ""}`}>
      <div className="history-head">
        <span>Historique</span>
        <button onClick={onClose} title="Masquer l'historique">
          ✕
        </button>
      </div>
      <button className="primary" onClick={onNewChat}>
        Nouveau chat
      </button>
      <div className="conv-list">
        {conversations.length === 0 && (
          <div className="conv-item" style={{ cursor: "default", opacity: 0.5 }}>
            Aucune conversation
          </div>
        )}
        {conversations.map((c) => (
          <div
            key={c.id}
            className={`conv-item ${c.id === selectedConv ? "selected" : ""}`}
            onClick={() => onSelectConv(c.id)}
            title={c.title}
          >
            {c.title}
            <button
              className="conv-del"
              title="Supprimer"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteConv(c.id);
              }}
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </aside>
  );
}

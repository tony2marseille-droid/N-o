import { Component, type ErrorInfo, type ReactNode } from "react";
import { getLang, t } from "../lib/i18n";

interface Props {
  children: ReactNode;
  viewKey?: string;
}

interface State {
  error: Error | null;
}

/**
 * Filet de securite : une erreur dans une vue affiche un message clair au
 * lieu de faire tomber toute l'application (blanc total). Se reinitialise
 * automatiquement quand la vue change (viewKey).
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[Néo] erreur de vue:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    if (prev.viewKey !== this.props.viewKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="content-scroll">
          <div className="empty-state">
            <div className="big">⚠</div>
            <div>{t(getLang(), "error.title")}</div>
            <div style={{ fontSize: 12, opacity: 0.7, maxWidth: 560, textAlign: "center" }}>
              {String(this.state.error.message || this.state.error)}
            </div>
            <button className="primary" onClick={() => window.location.reload()}>
              {t(getLang(), "error.reload")}
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

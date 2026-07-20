import { useCallback, useEffect, useState } from "react";
import { fetchSession, sendChat, type MealPlan } from "./api";
import { Chat, type ChatMessage } from "./components/Chat";
import { PlanPanel } from "./components/PlanPanel";

const SESSION_STORAGE_KEY = "chef-session-id";

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(() =>
    localStorage.getItem(SESSION_STORAGE_KEY),
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [plan, setPlan] = useState<MealPlan | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failedMessage, setFailedMessage] = useState<string | null>(null);

  // After a refresh the conversation text is gone (frontend-only), but the
  // backend still holds the session's plan — restore it. A 404 means the
  // backend restarted (in-memory sessions), so drop the stale id.
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    fetchSession(sessionId)
      .then((plan) => {
        if (!cancelled && plan.meals.length > 0) setPlan(plan);
      })
      .catch(() => {
        if (cancelled) return;
        localStorage.removeItem(SESSION_STORAGE_KEY);
        setSessionId(null);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount only
  }, []);

  const send = useCallback(
    async (text: string, options: { appendUser?: boolean } = {}) => {
      const message = text.trim();
      if (!message || pending) return;
      if (options.appendUser !== false) {
        setMessages((current) => [...current, { role: "user", text: message }]);
      }
      setPending(true);
      setError(null);
      setFailedMessage(null);
      try {
        const response = await sendChat(sessionId, message);
        setSessionId(response.session_id);
        localStorage.setItem(SESSION_STORAGE_KEY, response.session_id);
        setMessages((current) => [
          ...current,
          {
            role: "assistant",
            text: response.message,
            voiceSummary: response.voice_summary,
          },
        ]);
        if (response.meal_plan) setPlan(response.meal_plan);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "Something went wrong.");
        setFailedMessage(message);
      } finally {
        setPending(false);
      }
    },
    [pending, sessionId],
  );

  const retry = useCallback(() => {
    if (failedMessage) void send(failedMessage, { appendUser: false });
  }, [failedMessage, send]);

  const startNewConversation = useCallback(() => {
    localStorage.removeItem(SESSION_STORAGE_KEY);
    setSessionId(null);
    setMessages([]);
    setPlan(null);
    setError(null);
    setFailedMessage(null);
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Chef in My Pocket</h1>
          <p className="tagline">Your meal-planning assistant</p>
        </div>
        <button
          type="button"
          className="ghost-button"
          onClick={startNewConversation}
        >
          New conversation
        </button>
      </header>
      <main className="layout">
        <Chat
          messages={messages}
          pending={pending}
          error={error}
          onSend={(text) => void send(text)}
          onRetry={retry}
        />
        <PlanPanel plan={plan} />
      </main>
    </div>
  );
}

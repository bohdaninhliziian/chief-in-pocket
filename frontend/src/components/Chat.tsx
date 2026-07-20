import { useEffect, useRef } from "react";
import { Composer } from "./Composer";
import { SpeakButton } from "./SpeakButton";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  voiceSummary?: string;
}

const EXAMPLE_PROMPTS = [
  "Create a vegetarian meal plan for 5 days",
  "Create a high-protein plan for three days",
  "Exclude mushrooms from my meals",
];

interface ChatProps {
  messages: ChatMessage[];
  pending: boolean;
  error: string | null;
  onSend: (text: string) => void;
  onRetry: () => void;
}

export function Chat({ messages, pending, error, onSend, onRetry }: ChatProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages, pending]);

  return (
    <section className="chat" aria-label="Conversation">
      <div className="chat-scroll">
        {messages.length === 0 ? (
          <div className="welcome">
            <div className="welcome-icon" aria-hidden>
              🍳
            </div>
            <h2>What are we cooking this week?</h2>
            <p>
              Tell me a dietary goal and how many days to plan, and I&apos;ll
              build a meal plan with a shopping list.
            </p>
            <div className="examples">
              {EXAMPLE_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="example-chip"
                  onClick={() => onSend(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ol className="messages">
            {messages.map((message, index) => (
              <li key={index} className={`message message-${message.role}`}>
                {message.text}
                {message.role === "assistant" && message.voiceSummary ? (
                  <SpeakButton text={message.voiceSummary} />
                ) : null}
              </li>
            ))}
            {pending && (
              <li className="message message-assistant message-pending">
                <span className="dots" role="status" aria-label="Assistant is thinking">
                  <span />
                  <span />
                  <span />
                </span>
              </li>
            )}
          </ol>
        )}
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button type="button" className="ghost-button" onClick={onRetry}>
              Try again
            </button>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <Composer disabled={pending} onSend={onSend} />
    </section>
  );
}

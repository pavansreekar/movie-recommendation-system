"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiRequest } from "../lib/api";

function ChatIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SparkleIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4L12 2z"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function MovieCard({ rec, onClick }) {
  const m = rec.movie;
  return (
    <button className="chat-movie-card" onClick={() => onClick(m)} type="button">
      {m.poster_url ? (
        <img src={m.poster_url} alt={m.title} className="chat-movie-poster" />
      ) : (
        <div className="chat-movie-poster chat-movie-poster-fallback" />
      )}
      <div className="chat-movie-info">
        <span className="chat-movie-title">{m.title}</span>
        <span className="chat-movie-meta">
          {m.content_label}{m.year ? ` · ${m.year}` : ""}{m.rating ? ` · ★ ${m.rating.toFixed(1)}` : ""}
        </span>
        {m.genres?.length > 0 && (
          <span className="chat-movie-genres">{m.genres.slice(0, 3).join(", ")}</span>
        )}
      </div>
    </button>
  );
}

const SUGGESTED_PROMPTS = [
  "What should I watch tonight?",
  "A Telugu thriller on Netflix",
  "Something like Interstellar but emotional",
  "Best Hindi movies under 2 hours",
];

export default function ChatBot() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]); // [{role, content, recs?, error?}]
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  // Load persisted history when panel first opens
  useEffect(() => {
    if (!open || historyLoaded) return;
    setHistoryLoaded(true);
    apiRequest("/api/chat/history")
      .then((data) => {
        if (data?.history?.length) {
          setMessages(data.history.map((m) => ({ role: m.role, content: m.content })));
        }
      })
      .catch(() => {});
  }, [open, historyLoaded]);

  // Scroll to bottom whenever messages change
  useEffect(() => {
    if (open) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, open]);

  // Focus input when opening
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 80);
    }
  }, [open]);

  async function sendMessage(text) {
    const trimmed = (text || input).trim();
    if (!trimmed || loading) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
    setLoading(true);
    try {
      const data = await apiRequest("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message: trimmed }),
        timeoutMs: 60000, // chat can be slow — Claude + TMDB calls
      });
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: data.reply || "Here are some picks for you!",
          recs: data.recommendations || [],
          error: data.error || null,
        },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: e?.message || "Sorry, something went wrong. Please try again.",
          error: true,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function clearChat() {
    await apiRequest("/api/chat/clear", { method: "POST" }).catch(() => {});
    setMessages([]);
  }

  function handleSubmit(e) {
    e.preventDefault();
    sendMessage(input);
  }

  const isEmpty = messages.length === 0 && !loading;

  return (
    <>
      {/* Floating trigger button */}
      <button
        className={`chatbot-fab${open ? " chatbot-fab-open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close chat" : "Open chat assistant"}
      >
        {open ? <CloseIcon /> : <ChatIcon />}
        {!open && <span className="chatbot-fab-label">Chat (beta)</span>}
      </button>

      {/* Chat panel */}
      {open && (
        <div className="chatbot-panel" role="dialog" aria-label="Movie recommendation chat">
          {/* Header */}
          <div className="chatbot-header">
            <div className="chatbot-header-left">
              <span className="chatbot-header-icon"><SparkleIcon /></span>
              <div>
                <div className="chatbot-header-title">NextPick Assistant</div>
                <div className="chatbot-header-sub">Beta version</div>
              </div>
            </div>
            <div className="chatbot-header-actions">
              {messages.length > 0 && (
                <button className="chatbot-clear-btn" onClick={clearChat} title="Clear chat">
                  Clear
                </button>
              )}
              <button className="chatbot-close-btn" onClick={() => setOpen(false)} aria-label="Close">
                <CloseIcon />
              </button>
            </div>
          </div>

          {/* Messages area */}
          <div className="chatbot-messages">
            {isEmpty && (
              <div className="chatbot-empty">
                <div className="chatbot-empty-icon"><SparkleIcon /></div>
                <p className="chatbot-empty-title">What are you in the mood for?</p>
                <p className="chatbot-empty-sub">Describe what you want to watch and I'll find it.</p>
                <div className="chatbot-suggestions">
                  {SUGGESTED_PROMPTS.map((p) => (
                    <button key={p} className="chatbot-suggestion-chip" onClick={() => sendMessage(p)}>
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`chatbot-msg chatbot-msg-${msg.role}`}>
                {msg.role === "assistant" && (
                  <span className="chatbot-bot-avatar"><SparkleIcon /></span>
                )}
                <div className="chatbot-bubble">
                  <p className="chatbot-bubble-text">{msg.content}</p>
                  {msg.recs?.length > 0 && (
                    <div className="chatbot-recs">
                      {msg.recs.slice(0, 6).map((rec, j) => (
                        <MovieCard
                          key={j}
                          rec={rec}
                          onClick={(m) => {
                            setOpen(false);
                            router.push(`/title/${m.content_type}/${m.tmdb_id}`);
                          }}
                        />
                      ))}
                    </div>
                  )}
                  {msg.error && !msg.recs?.length && msg.role === "assistant" && (
                    <p className="chatbot-bubble-error">{typeof msg.error === "string" ? msg.error : ""}</p>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="chatbot-msg chatbot-msg-assistant">
                <span className="chatbot-bot-avatar"><SparkleIcon /></span>
                <div className="chatbot-bubble chatbot-bubble-typing">
                  <span /><span /><span />
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input area */}
          <form className="chatbot-input-row" onSubmit={handleSubmit}>
            <input
              ref={inputRef}
              className="chatbot-input"
              type="text"
              placeholder="e.g. Telugu thriller on Netflix…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
              autoComplete="off"
            />
            <button
              className="chatbot-send-btn"
              type="submit"
              disabled={loading || !input.trim()}
              aria-label="Send"
            >
              <SendIcon />
            </button>
          </form>
        </div>
      )}
    </>
  );
}

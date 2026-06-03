export const WATCH_HISTORY_EVENT = "watch-history-changed";

export function emitWatchHistoryChange(detail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(WATCH_HISTORY_EVENT, { detail }));
}

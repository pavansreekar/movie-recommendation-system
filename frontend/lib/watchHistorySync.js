export const WATCH_HISTORY_EVENT = "watch-history-changed";

export function emitWatchHistoryChange(detail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(WATCH_HISTORY_EVENT, { detail }));
}

// Fired when the watchlist or watched history was changed from outside the page
// showing it (the chat assistant), so that page can reload its list.
export const LIBRARY_EVENT = "library-changed";

export function emitLibraryChange() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(LIBRARY_EVENT));
}

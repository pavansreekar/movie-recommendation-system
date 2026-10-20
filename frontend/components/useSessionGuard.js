"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { apiRequest, peekCache } from "../lib/api";

export function useSessionGuard() {
  const [session, setSession] = useState(() => {
    // A session already fetched moments ago renders immediately instead of flashing a spinner.
    const known = peekCache("/api/session");
    return known?.authenticated
      ? { loading: false, authenticated: true, user: known.user || null }
      : { loading: true, authenticated: false, user: null };
  });
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    let active = true;
    apiRequest("/api/session")
      .then((data) => {
        if (!active) return;
        if (!data.authenticated && pathname !== "/") {
          router.replace("/");
          return;
        }
        setSession({ loading: false, authenticated: !!data.authenticated, user: data.user || null });
      })
      .catch(() => {
        if (!active) return;
        if (pathname !== "/") {
          router.replace("/");
          return;
        }
        setSession({ loading: false, authenticated: false, user: null });
      });
    return () => {
      active = false;
    };
  }, [pathname, router]);

  return session;
}

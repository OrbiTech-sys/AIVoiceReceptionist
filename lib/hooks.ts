"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

interface ApiState<T> {
  data: T | undefined;
  error: Error | null;
  loading: boolean;
}

/** Minimal data-fetching hook: loading / error / data with manual reload and optional polling. */
export function useApi<T>(path: string | null, opts: { poll?: number } = {}) {
  const [state, setState] = useState<ApiState<T>>({ data: undefined, error: null, loading: !!path });
  const [prevPath, setPrevPath] = useState(path);
  const [nonce, setNonce] = useState(0);

  // Mark as loading as soon as the path changes (adjusting state during render, not in an effect).
  if (path !== prevPath) {
    setPrevPath(path);
    setState((s) => ({ ...s, loading: !!path }));
  }

  useEffect(() => {
    if (!path) return;
    let alive = true;
    api<T>(path)
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error: Error) => alive && setState((s) => ({ ...s, error, loading: false })));
    return () => {
      alive = false;
    };
  }, [path, nonce]);

  useEffect(() => {
    if (!path || !opts.poll) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") setNonce((n) => n + 1);
    }, opts.poll);
    return () => clearInterval(id);
  }, [path, opts.poll]);

  const reload = useCallback((silent = false) => {
    if (!silent) setState((s) => ({ ...s, loading: true }));
    setNonce((n) => n + 1);
  }, []);
  const setData = useCallback((data: T) => setState((s) => ({ ...s, data })), []);

  return { ...state, reload, setData };
}

/** Local editable copy of server data that resets whenever the server data changes. */
export function useEditable<T>(source: T | undefined): [T | undefined, (v: T) => void] {
  const [value, setValue] = useState<T | undefined>(source);
  const [prev, setPrev] = useState(source);
  if (source !== prev) {
    setPrev(source);
    setValue(source);
  }
  return [value, setValue];
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

function readParam(key: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(key);
}

/** Reads ?key= from the URL on the client (avoids useSearchParams Suspense requirements). */
export function useQueryParam(key: string): [string | null, (v: string | null) => void] {
  const [value, setValue] = useState<string | null>(() => readParam(key));
  useEffect(() => {
    const read = () => setValue(readParam(key));
    // Client-side navigations may commit the new URL after the first render; re-read once it has.
    const t = setTimeout(read, 0);
    window.addEventListener("popstate", read);
    window.addEventListener("vr:urlchange", read);
    return () => {
      clearTimeout(t);
      window.removeEventListener("popstate", read);
      window.removeEventListener("vr:urlchange", read);
    };
  }, [key]);
  const update = useCallback(
    (v: string | null) => {
      const url = new URL(window.location.href);
      if (v === null) url.searchParams.delete(key);
      else url.searchParams.set(key, v);
      window.history.replaceState(window.history.state, "", url.toString());
      setValue(v);
    },
    [key],
  );
  return [value, update];
}

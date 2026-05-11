import { useCallback, useEffect, useState } from "react";

/**
 * Tiny localStorage-backed `useState`. Reads on mount, writes on every
 * setter call. Falls back to the default when the key is missing or the
 * stored payload is unparseable. Designed for sticky UI prefs (toolbar
 * toggles, layout choices); not durable storage.
 */
export function useLocalStorage<T>(
  key: string,
  defaultValue: T,
): [T, (next: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return defaultValue;
    try {
      const raw = window.localStorage.getItem(key);
      if (raw === null) return defaultValue;
      return JSON.parse(raw) as T;
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Ignore quota / privacy-mode errors.
    }
  }, [key, value]);

  const setter = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) =>
        typeof next === "function" ? (next as (p: T) => T)(prev) : next,
      );
    },
    [],
  );

  return [value, setter];
}

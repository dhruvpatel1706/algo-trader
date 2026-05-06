"use client";
import { useEffect, useRef, useState } from "react";
import type { Signal } from "./types";

export type WsEvent =
  | { type: "signal"; data: Signal }
  | { type: "fill"; data: Record<string, unknown> }
  | { type: "coherence_alert"; data: { agent: string; ratio: number; reason?: string } }
  | { type: "halt_event"; data: { reason: string; ts: string } };

export type WsState = "connecting" | "open" | "closed";

/**
 * Connect to the trading WebSocket with exponential backoff. Returns a stream
 * of typed events. If the host doesn't support WS or it can't be reached, we
 * stay in "closed" state and the caller can fall back to REST polling.
 */
export function useWsEvents(
  onEvent: (evt: WsEvent) => void,
  path: string = "/ws",
): WsState {
  const [state, setState] = useState<WsState>("connecting");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (typeof window === "undefined") return;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let ws: WebSocket | null = null;
    let cancelled = false;

    const wsUrl = () => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${window.location.host}${path}`;
    };

    const connect = () => {
      if (cancelled) return;
      setState("connecting");
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        scheduleReconnect();
        return;
      }
      ws.onopen = () => {
        attempt = 0;
        setState("open");
      };
      ws.onmessage = (e) => {
        try {
          const parsed = JSON.parse(typeof e.data === "string" ? e.data : "");
          if (parsed && typeof parsed.type === "string") {
            onEventRef.current(parsed as WsEvent);
          }
        } catch {
          // ignore non-JSON frames
        }
      };
      ws.onclose = () => {
        setState("closed");
        scheduleReconnect();
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          // ignore
        }
      };
    };

    const scheduleReconnect = () => {
      if (cancelled) return;
      attempt += 1;
      const delay = Math.min(30_000, 500 * 2 ** Math.min(attempt, 6));
      timer = setTimeout(connect, delay);
    };

    connect();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      try {
        ws?.close();
      } catch {
        // ignore
      }
    };
  }, [path]);

  return state;
}

/**
 * Convenience hook: keeps a ring buffer of the last N signals streamed from
 * the WS, seeded from REST history if provided.
 */
export function useSignalStream(seed: Signal[] = [], limit = 100) {
  const [signals, setSignals] = useState<Signal[]>(seed);
  const wsState = useWsEvents((evt) => {
    if (evt.type === "signal") {
      setSignals((prev) => [evt.data, ...prev].slice(0, limit));
    }
  });
  return { signals, wsState, setSignals };
}

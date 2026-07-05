import { useEffect, useState } from "react";

export interface StreamMsg {
  type: "log" | "progress" | "end";
  line?: string;
  event?: Record<string, unknown>;
}

export function useRunStream(runId: number | null, active: boolean) {
  const [logLines, setLogLines] = useState<string[]>([]);
  const [lastEvent, setLastEvent] = useState<Record<string, unknown> | null>(null);
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    if (runId == null || !active) return;
    setLogLines([]); setEnded(false);
    const es = new EventSource(`/api/runs/${runId}/stream`, { withCredentials: true });
    es.onmessage = (e) => {
      const msg: StreamMsg = JSON.parse(e.data);
      if (msg.type === "log" && msg.line) setLogLines((p) => [...p, msg.line!]);
      else if (msg.type === "progress") setLastEvent(msg.event ?? null);
      else if (msg.type === "end") { setEnded(true); es.close(); }
    };
    es.onerror = () => { es.close(); };  // caller re-fetches on reconnect
    return () => es.close();
  }, [runId, active]);

  return { logLines, lastEvent, ended };
}

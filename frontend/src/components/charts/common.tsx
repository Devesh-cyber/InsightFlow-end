import { useEffect, useRef, useState, type ReactNode } from "react";

/** Width of a container, so charts draw in real pixels (text never stretches). */
export function useWidth<T extends HTMLElement>(initial = 640) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(240, Math.floor(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export interface Tip { x: number; y: number; text: ReactNode }

export function useTip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const node = tip ? <div className="tooltip" style={{ left: tip.x, top: tip.y }}>{tip.text}</div> : null;
  return { tip, setTip, node };
}

/** "Nice" axis ticks: 0, 5, 10… instead of 0, 4.37, 8.74… */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step; // always reach past the largest value
  const ticks: number[] = [];
  for (let i = 0, v = start; v <= end + step * 1e-9 && i < 50; i++, v = start + i * step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}

export function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `${+(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${+(v / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${+(v / 1e3).toFixed(1)}k`;
  if (Number.isInteger(v)) return String(v);
  return String(+v.toPrecision(3));
}

/** Rounded-top bar path anchored to the baseline (4px data-end radius). */
export function barPath(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

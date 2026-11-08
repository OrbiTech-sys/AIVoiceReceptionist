"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import bellPoster from "@/components/marketing/assets/bell.webp";
import type { BellScene } from "@/components/marketing/bell-scene";
import { SAMPLE_CALL } from "@/components/marketing/content";

function canUse3d() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  if ((navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData) return false;
  try {
    const test = document.createElement("canvas");
    return !!(test.getContext("webgl2") ?? test.getContext("webgl"));
  } catch {
    return false;
  }
}

let audio: AudioContext | null = null;

// A short synthesized ding. It only ever plays in response to a press.
function ding() {
  try {
    audio ??= new AudioContext();
    if (audio.state === "suspended") void audio.resume();
    const now = audio.currentTime;
    const out = audio.createGain();
    out.gain.value = 0.14;
    out.connect(audio.destination);
    for (const [ratio, level, decay] of [
      [1, 1, 1.5],
      [2, 0.42, 0.9],
      [2.76, 0.26, 0.6],
      [5.4, 0.09, 0.3],
    ]) {
      const tone = audio.createOscillator();
      const env = audio.createGain();
      tone.frequency.value = 1760 * ratio;
      env.gain.setValueAtTime(level, now);
      env.gain.exponentialRampToValueAtTime(0.0001, now + decay);
      tone.connect(env).connect(out);
      tone.start(now);
      tone.stop(now + decay + 0.05);
    }
  } catch {
    // Sound is a nicety; the transcript still advances without it.
  }
}

export function BellStage() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<HTMLDivElement>(null);
  const bell = useRef<BellScene | null>(null);
  const [live, setLive] = useState(false);
  const [step, setStep] = useState(-1);

  const press = useCallback(() => {
    bell.current?.ring();
    ding();
    setStep((s) => (s + 1) % SAMPLE_CALL.length);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const view = viewRef.current;
    if (!canvas || !view || !canUse3d()) return;

    let cancelled = false;
    let cancelWait = () => {};

    // Safari has no requestIdleCallback.
    const whenIdle = (run: () => void) => {
      if (typeof window.requestIdleCallback === "function") {
        const id = window.requestIdleCallback(run, { timeout: 2500 });
        cancelWait = () => window.cancelIdleCallback(id);
      } else {
        const id = window.setTimeout(run, 600);
        cancelWait = () => window.clearTimeout(id);
      }
    };

    const start = async () => {
      await document.fonts.ready;
      const { createBellScene } = await import("@/components/marketing/bell-scene");
      if (cancelled) return;
      try {
        bell.current = createBellScene(canvas, {
          fontFamily: getComputedStyle(view).getPropertyValue("--font-display"),
          onReady: () => setLive(true),
          onPress: press,
          onLost: () => setLive(false),
        });
      } catch {
        setLive(false); // WebGL refused to start: the rendered picture stays in place.
      }
    };

    // three.js is downloaded only after the page has settled and the bell is on screen.
    const onScreen = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      onScreen.disconnect();
      whenIdle(() => void start());
    });
    onScreen.observe(view);

    return () => {
      cancelled = true;
      onScreen.disconnect();
      cancelWait();
      bell.current?.dispose();
      bell.current = null;
    };
  }, [press]);

  const line = step >= 0 ? SAMPLE_CALL[step] : null;

  return (
    <div className={`mk-stage mk-rise${live ? " is-live" : ""}`} style={{ "--i": 2 } as React.CSSProperties}>
      <div className="mk-stage__view" ref={viewRef}>
        <Image
          src={bellPoster}
          alt="A polished nickel service bell on a navy enamel base, lettered OrbiTech"
          className="mk-stage__poster"
          sizes="(min-width: 60rem) 614px, 512px"
          preload
        />
        <canvas ref={canvasRef} className="mk-stage__canvas" aria-hidden="true" />
      </div>
      <div className="mk-ring">
        <p className="mk-ring__line" aria-live="polite">
          {line ? (
            <>
              <span className="mk-ring__who">{line.who}</span>
              <span className="mk-ring__say">“{line.say}”</span>
            </>
          ) : (
            <span className="mk-ring__say">Press the bell to walk through an example call.</span>
          )}
        </p>
        <div className="mk-ring__foot">
          <span className="mk-ring__label">{line ? `Example call, line ${step + 1} of ${SAMPLE_CALL.length}` : "Example call"}</span>
          <button type="button" className="mk-btn mk-btn--quiet mk-btn--sm" onClick={press}>
            Ring the bell
          </button>
        </div>
      </div>
    </div>
  );
}

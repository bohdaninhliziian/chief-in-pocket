import { useEffect, useRef, useState } from "react";
import { speakText } from "../api";

type SpeakState = "idle" | "loading" | "playing";

// Only one message speaks at a time; starting a new playback stops the
// previous one, whichever button owns it.
let activeAudio: HTMLAudioElement | null = null;

function stopActiveAudio() {
  if (activeAudio) {
    activeAudio.pause();
    activeAudio = null;
  }
}

interface SpeakButtonProps {
  text: string;
}

export function SpeakButton({ text }: SpeakButtonProps) {
  const [state, setState] = useState<SpeakState>("idle");
  const [error, setError] = useState(false);
  // The blob URL is cached per message: replays never re-bill the TTS API.
  const blobUrlRef = useRef<string | null>(null);
  // Tracks the audio element this instance created, so unmount cleanup can
  // tell whether the module-level activeAudio belongs to this button.
  const ownAudioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    return () => {
      if (activeAudio === ownAudioRef.current) stopActiveAudio();
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    };
  }, []);

  const play = async () => {
    if (state === "loading") return;
    if (state === "playing") {
      stopActiveAudio();
      setState("idle");
      return;
    }
    setError(false);
    stopActiveAudio();
    try {
      if (!blobUrlRef.current) {
        setState("loading");
        const blob = await speakText(text);
        blobUrlRef.current = URL.createObjectURL(blob);
      }
      const audio = new Audio(blobUrlRef.current);
      activeAudio = audio;
      ownAudioRef.current = audio;
      audio.onended = () => {
        if (activeAudio === audio) activeAudio = null;
        setState("idle");
      };
      setState("playing");
      await audio.play();
    } catch {
      setError(true);
      setState("idle");
    }
  };

  const label = error
    ? "Could not play audio — try again"
    : state === "playing"
      ? "Stop playback"
      : "Play spoken summary";

  return (
    <button
      type="button"
      className="speak-button"
      onClick={() => void play()}
      disabled={state === "loading"}
      aria-label={label}
      title={label}
    >
      {state === "loading" ? "…" : error ? "⚠️" : state === "playing" ? "⏹" : "🔊"}
    </button>
  );
}

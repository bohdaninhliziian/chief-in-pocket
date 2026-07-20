import { useRef, useState } from "react";
import { transcribeAudio } from "../api";

type MicState = "idle" | "recording" | "transcribing";

// Below this the blob is a mic tap or silence, which STT models turn into
// hallucinated text — discard it client-side instead of transcribing.
const MIN_RECORDING_MS = 500;

interface ComposerProps {
  disabled: boolean;
  onSend: (text: string) => void;
}

function voiceSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined"
  );
}

export function Composer({ disabled, onSend }: ComposerProps) {
  const [value, setValue] = useState("");
  const [micState, setMicState] = useState<MicState>("idle");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recordingStartedAtRef = useRef(0);

  const submit = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const autoGrow = (element: HTMLTextAreaElement) => {
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 160)}px`;
  };

  const startRecording = async () => {
    setVoiceError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        void finishRecording();
      };
      recorderRef.current = recorder;
      recorder.start();
      recordingStartedAtRef.current = Date.now();
      setMicState("recording");
    } catch {
      setVoiceError("Microphone access was denied.");
      setMicState("idle");
    }
  };

  const finishRecording = async () => {
    if (Date.now() - recordingStartedAtRef.current < MIN_RECORDING_MS) {
      setVoiceError("Recording was too short — hold the mic a moment longer.");
      setMicState("idle");
      recorderRef.current = null;
      return;
    }
    if (chunksRef.current.length === 0) {
      // A muted or failed mic track produces no data; an empty blob would
      // reach the STT provider and come back as hallucinated text.
      setVoiceError("No audio was captured — check your microphone.");
      setMicState("idle");
      recorderRef.current = null;
      return;
    }
    setMicState("transcribing");
    try {
      const blob = new Blob(chunksRef.current, { type: "audio/webm" });
      const transcript = await transcribeAudio(blob);
      if (transcript) {
        // Into the input, never auto-sent: the user reviews and edits
        // the transcript (recipe names are easy to mis-transcribe).
        setValue((current) => (current ? `${current} ${transcript}` : transcript));
        textareaRef.current?.focus();
      }
    } catch (exc) {
      setVoiceError(
        exc instanceof Error ? exc.message : "Could not transcribe the recording.",
      );
    } finally {
      setMicState("idle");
      recorderRef.current = null;
    }
  };

  const toggleRecording = () => {
    if (micState === "recording") {
      recorderRef.current?.stop();
    } else if (micState === "idle") {
      void startRecording();
    }
  };

  const micTitle = !voiceSupported()
    ? "Voice input is not supported in this browser"
    : micState === "recording"
      ? "Stop recording"
      : "Record a voice message";

  return (
    <div className="composer-block">
      {voiceError && (
        <p className="voice-error" role="alert">
          {voiceError}
        </p>
      )}
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          placeholder="Message Chef in My Pocket…"
          aria-label="Message"
          onChange={(event) => {
            setValue(event.target.value);
            autoGrow(event.target);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <button
          type="button"
          className={`icon-button mic-button mic-${micState}`}
          title={micTitle}
          aria-label={micTitle}
          disabled={!voiceSupported() || micState === "transcribing" || disabled}
          onClick={toggleRecording}
        >
          {micState === "transcribing" ? "…" : micState === "recording" ? "■" : "🎙"}
        </button>
        <button
          type="submit"
          className="send-button"
          disabled={disabled || !value.trim()}
        >
          Send
        </button>
      </form>
    </div>
  );
}

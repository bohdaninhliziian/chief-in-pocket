import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SpeakButton } from "../components/SpeakButton";
import { speakText } from "../api";

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  speakText: vi.fn(),
}));

const mockSpeakText = vi.mocked(speakText);

interface AudioStub {
  play: ReturnType<typeof vi.fn>;
  pause: ReturnType<typeof vi.fn>;
  onended: (() => void) | null;
}

describe("SpeakButton", () => {
  let audioInstances: AudioStub[];
  // jsdom implements neither method; stub both so play() and the unmount
  // cleanup's revokeObjectURL call don't throw, and restore afterward so
  // the stub never leaks into other test files.
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;

  beforeEach(() => {
    audioInstances = [];
    mockSpeakText.mockReset();
    mockSpeakText.mockResolvedValue(new Blob(["audio"], { type: "audio/mpeg" }));
    vi.stubGlobal(
      "Audio",
      vi.fn().mockImplementation(() => {
        const instance: AudioStub = {
          play: vi.fn().mockResolvedValue(undefined),
          pause: vi.fn(),
          onended: null,
        };
        audioInstances.push(instance);
        return instance;
      }),
    );
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn(() => "blob:mock"),
      configurable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: vi.fn(),
      configurable: true,
    });
  });

  afterEach(() => {
    // Run React's unmount (and its cleanup effect, which calls
    // URL.revokeObjectURL) while the stub is still in place — the global
    // afterEach in test/setup.ts also calls cleanup(), but hooks run
    // innermost-first, so without this the stub would already be restored
    // by the time that later cleanup() unmounts the component.
    cleanup();
    vi.unstubAllGlobals();
    Object.defineProperty(URL, "createObjectURL", {
      value: originalCreateObjectURL,
      configurable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: originalRevokeObjectURL,
      configurable: true,
    });
  });

  it("fetches audio and plays it on click", async () => {
    render(<SpeakButton text="Váš plán je připraven." />);
    await userEvent.click(
      screen.getByRole("button", { name: /play spoken summary/i }),
    );
    await waitFor(() => expect(audioInstances).toHaveLength(1));
    expect(mockSpeakText).toHaveBeenCalledTimes(1);
    expect(mockSpeakText).toHaveBeenCalledWith("Váš plán je připraven.");
    expect(audioInstances[0].play).toHaveBeenCalled();
  });

  it("replays from cache without a second speakText call", async () => {
    render(<SpeakButton text="Ahoj" />);
    const button = screen.getByRole("button", { name: /play spoken summary/i });
    await userEvent.click(button);
    await waitFor(() => expect(audioInstances).toHaveLength(1));
    act(() => {
      audioInstances[0].onended?.(); // playback finishes
    });
    await userEvent.click(button);
    await waitFor(() => expect(audioInstances).toHaveLength(2));
    expect(mockSpeakText).toHaveBeenCalledTimes(1); // cached blob reused
  });

  it("stops the other button's audio when a second playback starts", async () => {
    // Each call must resolve a fresh Blob instance.
    mockSpeakText.mockImplementation(() =>
      Promise.resolve(new Blob(["audio"], { type: "audio/mpeg" })),
    );
    render(
      <>
        <SpeakButton text="první" />
        <SpeakButton text="druhá" />
      </>,
    );
    const buttons = screen.getAllByRole("button", {
      name: /play spoken summary/i,
    });
    await userEvent.click(buttons[0]);
    await waitFor(() => expect(audioInstances).toHaveLength(1));
    await userEvent.click(buttons[1]);
    await waitFor(() => expect(audioInstances).toHaveLength(2));
    expect(audioInstances[0].pause).toHaveBeenCalled();
    expect(audioInstances[1].play).toHaveBeenCalled();
  });

  it("shows an error state when the request fails", async () => {
    mockSpeakText.mockRejectedValue(new Error("Voice output is not configured"));
    render(<SpeakButton text="Ahoj" />);
    await userEvent.click(
      screen.getByRole("button", { name: /play spoken summary/i }),
    );
    expect(
      await screen.findByRole("button", { name: /could not play audio/i }),
    ).toBeInTheDocument();
  });
});

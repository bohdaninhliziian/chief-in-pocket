import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { ChatResponse, MealPlan, RecipeDetail } from "../api";
import { fetchRecipe, fetchSession, sendChat, transcribeAudio } from "../api";

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  sendChat: vi.fn(),
  fetchRecipe: vi.fn(),
  fetchSession: vi.fn(),
  transcribeAudio: vi.fn(),
}));

const mockSendChat = vi.mocked(sendChat);
const mockFetchRecipe = vi.mocked(fetchRecipe);
const mockFetchSession = vi.mocked(fetchSession);
const mockTranscribeAudio = vi.mocked(transcribeAudio);

const PLAN: MealPlan = {
  session_id: "s-1",
  dietary_goal: "high-protein",
  requested_days: 2,
  meals_per_day: 1,
  planned_days: 2,
  requested_meals: 2,
  planned_meals: 2,
  meals: [
    {
      day_index: 0,
      day_label: "Day 1",
      slot: 0,
      recipe_id: 10,
      recipe_name: "Hovězí guláš",
      goal: "high-protein",
    },
    {
      day_index: 1,
      day_label: "Day 2",
      slot: 0,
      recipe_id: 14,
      recipe_name: "Kuřecí polévka",
      goal: "high-protein",
    },
  ],
  excluded_ingredients: [],
  shopping_list: [
    { ingredient: "Hovězí plec", recipes: [10] },
    { ingredient: "Kuřecí prsa", recipes: [14] },
    { ingredient: "Sůl", recipes: [10, 14] },
  ],
};

function reply(overrides: Partial<ChatResponse> = {}): ChatResponse {
  return {
    session_id: "s-1",
    message: "Here is your plan.",
    voice_summary: "Here is your plan.",
    meal_plan: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("welcome state", () => {
  it("shows example prompts before the first message", () => {
    render(<App />);
    expect(screen.getByText("What are we cooking this week?")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create a vegetarian meal plan for 5 days" }),
    ).toBeInTheDocument();
  });

  it("sends an example prompt on click", async () => {
    mockSendChat.mockResolvedValue(reply({ message: "How many days?" }));
    render(<App />);
    await userEvent.click(
      screen.getByRole("button", { name: "Exclude mushrooms from my meals" }),
    );
    expect(mockSendChat).toHaveBeenCalledWith(null, "Exclude mushrooms from my meals");
    expect(await screen.findByText("How many days?")).toBeInTheDocument();
  });
});

describe("sending a message", () => {
  it("shows the user message and the assistant response", async () => {
    mockSendChat.mockResolvedValue(reply({ message: "Sounds great!" }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "I want vegetarian meals");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByText("I want vegetarian meals")).toBeInTheDocument();
    expect(await screen.findByText("Sounds great!")).toBeInTheDocument();
  });

  it("sends on Enter and inserts a newline on Shift+Enter", async () => {
    mockSendChat.mockResolvedValue(reply());
    render(<App />);
    const input = screen.getByLabelText("Message");
    await userEvent.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect(mockSendChat).not.toHaveBeenCalled();
    await userEvent.type(input, "{Enter}");
    expect(mockSendChat).toHaveBeenCalledWith(null, "line one\nline two");
  });

  it("shows a loading indicator while waiting for the backend", async () => {
    let resolve!: (value: ChatResponse) => void;
    mockSendChat.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "plan please{Enter}");
    expect(screen.getByRole("status", { name: "Assistant is thinking" })).toBeInTheDocument();
    resolve(reply({ message: "Done." }));
    expect(await screen.findByText("Done.")).toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: "Assistant is thinking" }),
    ).not.toBeInTheDocument();
  });

  it("persists the session id and reuses it for the next message", async () => {
    mockSendChat.mockResolvedValue(reply());
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "first{Enter}");
    await screen.findByText("Here is your plan.");
    expect(localStorage.getItem("chef-session-id")).toBe("s-1");
    await userEvent.type(screen.getByLabelText("Message"), "second{Enter}");
    expect(mockSendChat).toHaveBeenLastCalledWith("s-1", "second");
  });
});

describe("speaker button", () => {
  it("renders after a reply that carries a voice summary", async () => {
    mockSendChat.mockResolvedValue(reply({ voice_summary: "Krátké shrnutí." }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "hello{Enter}");
    expect(
      await screen.findByRole("button", { name: /play spoken summary/i }),
    ).toBeInTheDocument();
  });

  it("does not render when the reply's voice summary is empty", async () => {
    mockSendChat.mockResolvedValue(reply({ voice_summary: "" }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "hello{Enter}");
    await screen.findByText("Here is your plan.");
    expect(
      screen.queryByRole("button", { name: /play spoken summary/i }),
    ).not.toBeInTheDocument();
  });
});

describe("error handling", () => {
  it("shows a friendly error and retries without duplicating the message", async () => {
    mockSendChat.mockRejectedValueOnce(new Error("The assistant is temporarily unavailable."));
    mockSendChat.mockResolvedValueOnce(reply({ message: "Recovered!" }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "hello{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("temporarily unavailable");

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Recovered!")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // the user message appears exactly once despite the retry
    expect(screen.getAllByText("hello")).toHaveLength(1);
    expect(mockSendChat).toHaveBeenCalledTimes(2);
  });
});

describe("meal plan panel", () => {
  it("shows an empty state before a plan exists", () => {
    render(<App />);
    expect(screen.getByText("No meal plan yet")).toBeInTheDocument();
  });

  it("renders days, recipe names and goals when the backend returns a plan", async () => {
    mockSendChat.mockResolvedValue(reply({ meal_plan: PLAN }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "two days{Enter}");
    await screen.findByRole("complementary", { name: "Meal plan" });
    const days = screen.getByRole("region", { name: "Plan days" });
    expect(within(days).getByText("Day 1")).toBeInTheDocument();
    expect(within(days).getByText("Hovězí guláš")).toBeInTheDocument();
    expect(within(days).getByText("Day 2")).toBeInTheDocument();
    expect(within(days).getAllByText("high-protein").length).toBeGreaterThan(0);
  });

  it("opens meal details in a modal and closes it again", async () => {
    const detail: RecipeDetail = {
      id: 10,
      name: "Hovězí guláš",
      author: "Roman Vaněk",
      description: "Popis.",
      ingredients: ["Hovězí plec", "Cibule"],
      instructions: ["Orestujeme cibuli.", "Přidáme maso."],
      supported_goals: ["high-protein"],
      allergens: [],
      meal_type: "main-course",
    };
    mockSendChat.mockResolvedValue(reply({ meal_plan: PLAN }));
    mockFetchRecipe.mockResolvedValue(detail);
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "two days{Enter}");
    const panel = await screen.findByRole("complementary", { name: "Meal plan" });
    await userEvent.click(
      within(panel).getByRole("button", { name: /Hovězí guláš/ }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Hovězí guláš" });
    expect(await within(dialog).findByText("Orestujeme cibuli.")).toBeInTheDocument();
    expect(within(dialog).getByText("Cibule")).toBeInTheDocument();
    expect(within(dialog).getByText(/Roman Vaněk/)).toBeInTheDocument();
    expect(mockFetchRecipe).toHaveBeenCalledWith(10);

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // reopening uses the cache — no second fetch
    await userEvent.click(
      within(panel).getByRole("button", { name: /Hovězí guláš/ }),
    );
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(mockFetchRecipe).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Close recipe" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("groups multiple meals per day under one day heading", async () => {
    const multiPlan: MealPlan = {
      ...PLAN,
      meals_per_day: 2,
      planned_days: 1,
      meals: [
        { ...PLAN.meals[0], slot: 0 },
        { ...PLAN.meals[1], day_index: 0, day_label: "Day 1", slot: 1 },
      ],
    };
    mockSendChat.mockResolvedValue(reply({ meal_plan: multiPlan }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "one day{Enter}");
    await screen.findByRole("complementary", { name: "Meal plan" });
    const days = screen.getByRole("region", { name: "Plan days" });
    expect(within(days).getAllByText("Day 1")).toHaveLength(1); // one heading
    expect(within(days).getByText("Meal 1")).toBeInTheDocument();
    expect(within(days).getByText("Meal 2")).toBeInTheDocument();
    expect(within(days).getByText("Hovězí guláš")).toBeInTheDocument();
    expect(within(days).getByText("Kuřecí polévka")).toBeInTheDocument();
    expect(within(days).getByText(/2 meals\/day/)).toBeInTheDocument();
  });
});

describe("shopping list", () => {
  it("renders items with working frontend-only checkboxes", async () => {
    mockSendChat.mockResolvedValue(reply({ meal_plan: PLAN }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "two days{Enter}");
    expect(await screen.findByText("Shopping list")).toBeInTheDocument();
    expect(screen.getByText("0/3 collected")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: /Hovězí plec/ }));
    expect(screen.getByText("1/3 collected")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Hovězí plec/ })).toBeChecked();
  });

  it("shows a dish badge per related recipe on each item", async () => {
    mockSendChat.mockResolvedValue(reply({ meal_plan: PLAN }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "two days{Enter}");
    const shopping = await screen.findByRole("region", { name: "Shopping list" });

    const salt = within(shopping).getByText("Sůl").closest("label");
    expect(salt).not.toBeNull();
    expect(within(salt!).getByText("Hovězí guláš")).toBeInTheDocument();
    expect(within(salt!).getByText("Kuřecí polévka")).toBeInTheDocument();

    const beef = within(shopping).getByText("Hovězí plec").closest("label");
    expect(within(beef!).getByText("Hovězí guláš")).toBeInTheDocument();
    expect(within(beef!).queryByText("Kuřecí polévka")).not.toBeInTheDocument();
  });

  it("updates the list when a new plan arrives", async () => {
    mockSendChat.mockResolvedValueOnce(reply({ meal_plan: PLAN }));
    const updated: MealPlan = {
      ...PLAN,
      meals: [
        PLAN.meals[0],
        { ...PLAN.meals[1], recipe_id: 40, recipe_name: "Salát s tofu", goal: "vegetarian" },
      ],
      shopping_list: [
        { ingredient: "Hovězí plec", recipes: [10] },
        { ingredient: "Tofu", recipes: [40] },
      ],
    };
    mockSendChat.mockResolvedValueOnce(reply({ message: "Replaced.", meal_plan: updated }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "two days{Enter}");
    await screen.findByText("Kuřecí prsa");
    await userEvent.type(screen.getByLabelText("Message"), "replace tuesday{Enter}");
    expect(await screen.findByText("Tofu")).toBeInTheDocument();
    expect(screen.queryByText("Kuřecí prsa")).not.toBeInTheDocument();
    expect(screen.getAllByText("Salát s tofu").length).toBeGreaterThan(0);
  });
});

describe("session restore on refresh", () => {
  it("restores the meal plan for a stored session id", async () => {
    localStorage.setItem("chef-session-id", "s-1");
    mockFetchSession.mockResolvedValue(PLAN);
    render(<App />);
    expect((await screen.findAllByText("Hovězí guláš")).length).toBeGreaterThan(0);
    expect(mockFetchSession).toHaveBeenCalledWith("s-1");
  });

  it("drops a stale session id when the backend forgot it", async () => {
    localStorage.setItem("chef-session-id", "gone");
    mockFetchSession.mockRejectedValue(new Error("unknown session"));
    render(<App />);
    await waitFor(() =>
      expect(localStorage.getItem("chef-session-id")).toBeNull(),
    );
    expect(screen.getByText("No meal plan yet")).toBeInTheDocument();
  });
});

describe("session controls", () => {
  it("clears everything on new conversation", async () => {
    mockSendChat.mockResolvedValue(reply({ meal_plan: PLAN }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "two days{Enter}");
    await screen.findByText("Shopping list");

    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.getByText("What are we cooking this week?")).toBeInTheDocument();
    expect(screen.getByText("No meal plan yet")).toBeInTheDocument();
    expect(localStorage.getItem("chef-session-id")).toBeNull();
  });
});

describe("voice input", () => {
  it("disables the mic when the browser lacks recording support", () => {
    render(<App />); // jsdom has no MediaRecorder
    expect(
      screen.getByRole("button", {
        name: "Voice input is not supported in this browser",
      }),
    ).toBeDisabled();
  });

  describe("with recording support", () => {
    class FakeMediaRecorder {
      static emitData = true;
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      constructor(_stream: unknown) {}
      start() {}
      stop() {
        if (FakeMediaRecorder.emitData) {
          this.ondataavailable?.({
            data: new Blob(["fake-audio"], { type: "audio/webm" }),
          });
        }
        this.onstop?.();
      }
    }

    // The recorded duration is measured with Date.now(); pin the clock so
    // the too-short / long-enough branches are deterministic, not a race
    // against the test runner.
    let now = 0;
    let nowSpy: { mockRestore(): void };

    beforeEach(() => {
      now = 0;
      nowSpy = vi.spyOn(Date, "now").mockImplementation(() => now);
      FakeMediaRecorder.emitData = true;
      vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: {
          getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [] }),
        },
      });
    });

    afterEach(() => {
      nowSpy.mockRestore();
      vi.unstubAllGlobals();
      Reflect.deleteProperty(navigator, "mediaDevices");
    });

    const record = async (durationMs: number) => {
      await userEvent.click(
        screen.getByRole("button", { name: "Record a voice message" }),
      );
      now += durationMs;
      await userEvent.click(
        await screen.findByRole("button", { name: "Stop recording" }),
      );
    };

    it("discards a too-short recording without calling the API", async () => {
      render(<App />);
      await record(100);

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Recording was too short",
      );
      expect(mockTranscribeAudio).not.toHaveBeenCalled();
    });

    it("discards a recording that captured no audio", async () => {
      FakeMediaRecorder.emitData = false;
      render(<App />);
      await record(2000);

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "No audio was captured",
      );
      expect(mockTranscribeAudio).not.toHaveBeenCalled();
    });

    it("puts the transcript of a long-enough recording into the input", async () => {
      mockTranscribeAudio.mockResolvedValue("Chci vegetariánský plán");
      render(<App />);
      await record(2000);

      await waitFor(() =>
        expect(screen.getByLabelText("Message")).toHaveValue(
          "Chci vegetariánský plán",
        ),
      );
      expect(mockSendChat).not.toHaveBeenCalled(); // never auto-sent
    });
  });
});

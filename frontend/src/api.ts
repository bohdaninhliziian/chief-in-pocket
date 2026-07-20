/** Typed client for the Chef in My Pocket chat API. */

export interface Meal {
  day_index: number;
  day_label: string;
  slot: number;
  recipe_id: number;
  recipe_name: string;
  goal: string;
}

export interface ShoppingItem {
  ingredient: string;
  recipes: number[];
}

export interface MealPlan {
  session_id: string;
  dietary_goal: string | null;
  requested_days: number | null;
  meals_per_day: number | null;
  planned_days: number;
  requested_meals: number | null;
  planned_meals: number;
  meals: Meal[];
  excluded_ingredients: string[];
  shopping_list: ShoppingItem[];
}

export interface ChatResponse {
  session_id: string;
  message: string;
  voice_summary: string;
  meal_plan: MealPlan | null;
}

export interface RecipeDetail {
  id: number;
  name: string;
  author: string | null;
  description: string;
  ingredients: string[];
  instructions: string[];
  supported_goals: string[];
  allergens: string[];
  meal_type: string | null;
}

const API_BASE: string =
  (import.meta.env?.VITE_API_BASE as string | undefined) ?? "http://127.0.0.1:8000";

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // fall through to the generic message
  }
  return "Something went wrong. Please try again.";
}

export async function sendChat(
  sessionId: string | null,
  message: string,
): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()) as ChatResponse;
}

/** Restore a session's structured state (used after a page refresh). */
export async function fetchSession(sessionId: string): Promise<MealPlan> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}`);
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()) as MealPlan;
}

export async function fetchRecipe(recipeId: number): Promise<RecipeDetail> {
  const response = await fetch(`${API_BASE}/recipes/${recipeId}`);
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()) as RecipeDetail;
}

/** Fetch spoken audio (mp3) for a short text via the backend TTS proxy. */
export async function speakText(text: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}/speak`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return await response.blob();
}

export async function transcribeAudio(audio: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", audio, "voice-message.webm");
  const response = await fetch(`${API_BASE}/transcribe`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(await parseError(response));
  const body = (await response.json()) as { text: string };
  return body.text;
}

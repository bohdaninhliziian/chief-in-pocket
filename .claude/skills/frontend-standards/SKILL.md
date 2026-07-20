---
name: frontend-standards
description: Coding standards for the Chef in My Pocket frontend - React 19 + TypeScript strict + Vite conventions, component patterns, API-client rules, styling system, and testing approach. Load when writing or refactoring any code in frontend/.
---

# Frontend standards for Chef in My Pocket

The frontend is one desktop-only chat page (`frontend/`, Vite + React 19 +
TypeScript strict). It is a thin view over backend state: the chat drives,
the right panel renders whatever `meal_plan` the backend returned. Keep it
that way.

## Non-negotiables

- **Never reconstruct state client-side.** The meal plan and shopping list
  come whole from `POST /chat` (`meal_plan`) and `GET /recipes/{id}`. No
  parsing of assistant text, no client-side plan edits.
- **Voice is an input adapter.** Recording → `POST /transcribe` → transcript
  into the composer input → user edits → user presses Send. Never auto-send
  a transcript; never create a second conversation path.
- **All HTTP in `src/api.ts`.** Typed functions, snake_case payload types
  mirroring the backend models, `VITE_API_BASE` (default
  `http://127.0.0.1:8000`). Components never call `fetch` directly.
- **Session id**: `localStorage["chef-session-id"]`, written only from a
  successful response, cleared by "New conversation". The backend forgets
  in-memory sessions on restart — the UI must degrade gracefully (a fresh
  blank session), never crash.

## Components

- Function components, explicit `interface XProps`, no `React.FC`.
- `App` owns conversation state (messages, pending, error, session, plan);
  child components own only local UI state (expansion, checkboxes, input
  value). Callbacks down, no context/state libraries at this size.
- Keep the component list small (Chat, Composer, PlanPanel, MealCard,
  ShoppingList); prefer extending an existing component over adding one.
- Loading, error and empty states are part of every data-bearing component,
  not afterthoughts. Errors are friendly sentences with a retry affordance;
  a retry must not duplicate the user's message.

## Styling

- Hand-rolled CSS in `src/styles.css` only — no frameworks, no CSS-in-JS.
  Use the `:root` variables (`--bg`, `--surface`, `--accent`, `--radius`…);
  add a variable rather than hard-coding a new color.
- Assistant feel, not admin dashboard: generous spacing, 12-16px radii,
  subtle shadows, one accent color. Desktop-only (min-width 960px) — no
  media queries.
- Czech text everywhere in data; ensure nothing breaks on diacritics or
  long recipe names (ellipsis where needed).

## Tests (Vitest + Testing Library)

- Tests live in `src/__tests__/`; setup in `src/test/setup.ts` (jest-dom,
  cleanup, localStorage reset).
- Mock the `../api` module with `vi.mock` — never real network. Drive the
  UI with `@testing-library/user-event`; query by role/label, not test ids.
- Every main flow stays covered: send message, receive response, loading
  indicator, error + retry, plan render, meal expand, shopping checkbox,
  session persistence, new conversation.
- jsdom has no `MediaRecorder`/`scrollIntoView` — guard browser-only APIs
  (`?.` / capability checks) so components stay testable.

## The gates — run from `frontend/` before claiming anything done

```bash
npm run typecheck   # tsc --noEmit, strict
npm test            # vitest run
npm run build       # production build must succeed
```

All three green, or the work is not finished. After frontend changes, run
the **frontend-code-reviewer** agent.

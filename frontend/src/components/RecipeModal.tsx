import { useEffect, useRef } from "react";
import type { Meal, RecipeDetail } from "../api";

interface RecipeModalProps {
  meal: Meal;
  detail: RecipeDetail | undefined;
  error: string | null;
  onClose: () => void;
}

export function RecipeModal({ meal, detail, error, onClose }: RecipeModalProps) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={meal.recipe_name}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <h3 className="modal-title">{meal.recipe_name}</h3>
            <p className="modal-meta">
              {meal.day_label}
              {" · "}
              <span className="tag">{meal.goal}</span>
              {detail?.author ? ` · ${detail.author}` : ""}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-button"
            aria-label="Close recipe"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        {!detail && !error && <p className="muted">Loading recipe…</p>}
        {error && <p className="muted">{error}</p>}
        {detail && (
          <div className="meal-detail">
            {detail.description && <p>{detail.description}</p>}
            <h4>Ingredients</h4>
            <ul className="ingredient-list">
              {detail.ingredients.map((ingredient) => (
                <li key={ingredient}>{ingredient}</li>
              ))}
            </ul>
            <h4>Instructions</h4>
            <ol className="instruction-list">
              {detail.instructions.map((step, index) => (
                <li key={index}>{step}</li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}

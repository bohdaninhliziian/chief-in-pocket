import type { Meal } from "../api";

interface MealCardProps {
  meal: Meal;
  showSlot: boolean;
  onOpen: (meal: Meal) => void;
}

export function MealCard({ meal, showSlot, onOpen }: MealCardProps) {
  return (
    <li className="meal-card">
      <button
        type="button"
        className="meal-header"
        title="Show recipe"
        onClick={() => onOpen(meal)}
      >
        {showSlot && <span className="meal-slot">Meal {meal.slot + 1}</span>}
        <span className="meal-name" title={meal.recipe_name}>
          {meal.recipe_name}
        </span>
        <span className="meal-tags">
          <span className="tag">{meal.goal}</span>
          <span className="chevron" aria-hidden>
            ▸
          </span>
        </span>
      </button>
    </li>
  );
}

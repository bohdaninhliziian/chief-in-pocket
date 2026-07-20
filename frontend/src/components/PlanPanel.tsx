import { useState } from "react";
import { fetchRecipe, type Meal, type MealPlan, type RecipeDetail } from "../api";
import { MealCard } from "./MealCard";
import { RecipeModal } from "./RecipeModal";
import { ShoppingList } from "./ShoppingList";

interface PlanPanelProps {
  plan: MealPlan | null;
}

function groupByDay(meals: Meal[]): [string, Meal[]][] {
  const days = new Map<string, Meal[]>();
  for (const meal of meals) {
    const group = days.get(meal.day_label) ?? [];
    group.push(meal);
    days.set(meal.day_label, group);
  }
  return [...days.entries()];
}

export function PlanPanel({ plan }: PlanPanelProps) {
  const [details, setDetails] = useState<Record<number, RecipeDetail>>({});
  const [selected, setSelected] = useState<Meal | null>(null);
  const [errorRecipeId, setErrorRecipeId] = useState<number | null>(null);

  const openMeal = (meal: Meal) => {
    setSelected(meal);
    setErrorRecipeId(null);
    if (details[meal.recipe_id]) return;
    fetchRecipe(meal.recipe_id)
      .then((detail) =>
        setDetails((current) => ({ ...current, [meal.recipe_id]: detail })),
      )
      .catch(() => setErrorRecipeId(meal.recipe_id));
  };

  if (!plan || plan.meals.length === 0) {
    return (
      <aside className="plan-panel" aria-label="Meal plan">
        <div className="panel-empty">
          <div aria-hidden>🗓️</div>
          <h3>No meal plan yet</h3>
          <p>Ask for a plan in the chat and it will show up here.</p>
        </div>
      </aside>
    );
  }

  const showSlots = plan.meals.some((meal) => meal.slot > 0);
  const recipeNames = Object.fromEntries(
    plan.meals.map((meal) => [meal.recipe_id, meal.recipe_name]),
  );

  return (
    <aside className="plan-panel" aria-label="Meal plan">
      <section aria-label="Plan days">
        <div className="panel-heading">
          <h3>Meal plan</h3>
          <span className="panel-sub">
            {plan.dietary_goal} · {plan.planned_days}
            {plan.requested_days && plan.requested_days !== plan.planned_days
              ? ` of ${plan.requested_days}`
              : ""}{" "}
            days
            {plan.meals_per_day && plan.meals_per_day > 1
              ? ` · ${plan.meals_per_day} meals/day`
              : ""}
          </span>
        </div>
        <div className="day-groups">
          {groupByDay(plan.meals).map(([dayLabel, meals]) => (
            <div key={dayLabel} className="day-group">
              <h4 className="day-heading">{dayLabel}</h4>
              <ul className="meal-list">
                {meals.map((meal) => (
                  <MealCard
                    key={`${meal.day_index}-${meal.slot}`}
                    meal={meal}
                    showSlot={showSlots}
                    onOpen={openMeal}
                  />
                ))}
              </ul>
            </div>
          ))}
        </div>
        {plan.excluded_ingredients.length > 0 && (
          <p className="exclusions">
            Avoiding: {plan.excluded_ingredients.join(", ")}
          </p>
        )}
      </section>
      <ShoppingList items={plan.shopping_list} recipeNames={recipeNames} />
      {selected && (
        <RecipeModal
          meal={selected}
          detail={details[selected.recipe_id]}
          error={
            errorRecipeId === selected.recipe_id
              ? "Could not load the recipe details."
              : null
          }
          onClose={() => setSelected(null)}
        />
      )}
    </aside>
  );
}

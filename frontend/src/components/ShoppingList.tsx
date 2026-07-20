import { useState } from "react";
import type { ShoppingItem } from "../api";

interface ShoppingListProps {
  items: ShoppingItem[];
  recipeNames: Record<number, string>;
}

export function ShoppingList({ items, recipeNames }: ShoppingListProps) {
  // Checkbox state is frontend-only by design; keyed by ingredient name so
  // it survives plan updates that keep the same ingredient.
  const [checked, setChecked] = useState<Record<string, boolean>>({});

  if (items.length === 0) return null;
  const done = items.filter((item) => checked[item.ingredient]).length;

  return (
    <section className="shopping" aria-label="Shopping list">
      <div className="panel-heading">
        <h3>Shopping list</h3>
        <span className="panel-sub">
          {done}/{items.length} collected
        </span>
      </div>
      <ul className="shopping-list">
        {items.map((item) => (
          <li key={item.ingredient}>
            <label
              className={checked[item.ingredient] ? "item item-checked" : "item"}
            >
              <input
                type="checkbox"
                checked={checked[item.ingredient] ?? false}
                onChange={() =>
                  setChecked((current) => ({
                    ...current,
                    [item.ingredient]: !current[item.ingredient],
                  }))
                }
              />
              <span className="item-name">{item.ingredient}</span>
              <span className="item-badges">
                {item.recipes.map((recipeId) => {
                  const name = recipeNames[recipeId];
                  return name ? (
                    <span key={recipeId} className="badge" title={name}>
                      {name}
                    </span>
                  ) : null;
                })}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </section>
  );
}

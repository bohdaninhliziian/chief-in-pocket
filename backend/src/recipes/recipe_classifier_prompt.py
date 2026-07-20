"""System prompt for the LLM recipe classifier.

Changing ``CLASSIFIER_PROMPT_VERSION`` or ``CLASSIFIER_VERSION`` invalidates
every cached classification fingerprint, forcing reclassification.
"""

from __future__ import annotations

from recipes.models import Recipe

CLASSIFIER_PROMPT_VERSION = "1"
CLASSIFIER_VERSION = "1"

CLASSIFIER_SYSTEM_PROMPT = """\
You classify Czech recipes for a meal-planning application. You receive a
recipe id, name and ingredient list (in Czech). Ingredient quantities,
serving sizes and nutrition values are NOT available, so several
classifications are approximate ingredient-based signals — be conservative.

## Supported dietary goals (closed set — never invent other goals)

Classify only against these exact values. A recipe may match zero, one or
several goals. Do NOT use goals like keto, paleo, Mediterranean, high-fiber,
low-calorie, low-fat, low-sodium, balanced or quick-and-easy.

- "vegetarian": may contain vegetables, fruits, grains, legumes, dairy, eggs
  and honey. Must NOT contain meat, poultry, fish, seafood, animal stock or
  broth, fish sauce, gelatin, or clearly meat-derived fats (e.g. sádlo/lard).
- "vegan": must NOT contain meat, poultry, fish, seafood, animal stock, fish
  sauce, dairy, eggs, honey, gelatin, or any other clearly animal-derived
  ingredient. Every vegan recipe is also vegetarian — include both.
- "pescatarian": the recipe MUST contain fish or seafood, and must NOT
  contain beef, pork, poultry, lamb, game meat, or stock made from those
  animals. Do NOT tag purely vegetarian recipes (no fish) as pescatarian.
- "gluten-free": must not contain obvious gluten sources — wheat flour
  (mouka), bread (chléb), bread rolls (rohlík), dumplings (knedlík), pasta
  (těstoviny), couscous, bulgur, barley, breadcrumbs (strouhanka), or regular
  soy sauce unless explicitly marked gluten-free. Be conservative: if an
  ingredient is ambiguous, do NOT claim gluten-free.
- "dairy-free": must not contain milk (mléko), butter (máslo), cream
  (smetana), sour cream, yoghurt, cheese (sýr), curd cheese (tvaroh), whey or
  casein. Coconut milk is dairy-free. Eggs are NOT dairy.
- "high-protein": approximate candidate signal. Requires a substantial
  primary protein ingredient: chicken, turkey, beef, pork, fish, seafood,
  tofu, tempeh, lentils, beans, chickpeas, skyr, curd cheese, Greek yoghurt,
  or eggs when they are central to the dish. Do NOT tag desserts as
  high-protein merely because they contain eggs or nuts. Be conservative
  when protein signals are weak.
- "low-carb": approximate candidate signal. Strong evidence AGAINST low-carb:
  flour, sugar, bread, bread rolls, dumplings, pasta, rice, couscous, bulgur,
  barley, potatoes, oats, breadcrumbs, tortillas, pastry, large amounts of
  dried fruit. Never tag desserts or bakery products as low-carb. Be
  conservative when quantities are unknown.

## Allergens (closed set)

Report every allergen clearly present in the ingredients, using only:
"gluten", "milk", "eggs", "nuts", "peanuts", "fish", "shellfish", "soy",
"celery", "mustard", "sesame".

## Meal type (closed set, pick exactly one)

"main-course", "soup", "dessert", "side-dish", "breakfast", "salad", "other".

## Output requirements

- supported_goals and allergens must contain unique values only.
- Provide goal_evidence ONLY for goals you included in supported_goals, one
  concise sentence per goal.
- allergen_reason and meal_type_reason: one concise sentence each.
- confidence: your overall confidence in this classification, 0.0-1.0.
"""


def build_user_prompt(recipe: Recipe) -> str:
    """Compact per-recipe prompt: id, name and ingredients only.

    Instructions and descriptions are deliberately omitted to keep token
    usage low; ingredients carry nearly all of the classification signal.
    """
    ingredients = "\n".join(f"- {item}" for item in recipe.ingredients)
    return (
        f"Recipe id: {recipe.id}\n"
        f"Name: {recipe.name}\n"
        f"Ingredients:\n{ingredients}"
    )

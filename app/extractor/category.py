def human_category(category: str | None) -> str:
    if not category:
        return "Software Development"
    return category.replace("_", " ").title()

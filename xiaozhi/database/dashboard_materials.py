"""Projections used by the dashboard material list."""

PAGE_SIZE = 20
PREVIEW_LENGTH = 160


def material_page_queries(placeholder, owner_id, query, category):
    conditions = [f"owner_id = {placeholder}"]
    params = [int(owner_id)]
    if category:
        conditions.append(f"category = {placeholder}")
        params.append(category)
    if query:
        conditions.append(
            "LOWER(COALESCE(title, '') || ' ' || COALESCE(category, '') || ' ' || "
            f"COALESCE(content, '') || ' ' || COALESCE(keywords, '')) LIKE {placeholder} ESCAPE '!'"
        )
        escaped = query.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        params.append(f"%{escaped}%")
    where = " AND ".join(conditions)
    return (
        f"SELECT COUNT(*) AS total FROM materials WHERE {where}",
        f"""SELECT id, title, category, keywords, source_type, api_url_ciphertext,
            SUBSTR(content, 1, {PREVIEW_LENGTH + 1}) AS content
            FROM materials WHERE {where} ORDER BY id DESC LIMIT {placeholder} OFFSET {placeholder}""",
        tuple(params),
    )


def summary_query(placeholder):
    fields = ("tugas", "pengumuman", "materi")
    counts = ", ".join(
        f"COUNT(CASE WHEN LOWER(category) LIKE '%{field}%' THEN 1 END) AS {field}"
        for field in fields
    )
    # Psycopg requires escaping literal percent signs beside placeholders.
    if placeholder == "%s":
        counts = counts.replace("%", "%%")
    return f"SELECT COUNT(*) AS total, {counts} FROM materials WHERE owner_id = {placeholder}"


def clamp_page(total, page):
    return min(max(page, 1), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))

// The public and archived history pages both contain a single static table.
(() => {
  const table = document.querySelector("main table");
  const headerRow = table?.tHead?.rows[0];
  const body = table?.tBodies[0];
  if (!headerRow || !body?.rows.length) return;

  const collator = new Intl.Collator("en", { sensitivity: "base" });
  const textFields = new Set([
    "school", "team", "conference", "kind", "tournament champion", "runner-up",
  ]);
  const rows = Array.from(body.rows);
  let activeHeader;
  let ascending = true;

  function key(text, field) {
    if (!text || text === "—") return null;
    if (textFields.has(field)) return text;
    if (field === "record" || field === "final score") {
      const parts = /^(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?/.exec(text);
      return parts ? [Number(parts[1]), Number(parts[2]), Number(parts[3] ?? 0)] : null;
    }
    if (field === "season" || field === "year") {
      const span = /^(\d{4})\s*[–-]\s*\d{2}$/.exec(text);
      if (span) return Number(span[1]);
    }
    const value = Number(text);
    return Number.isFinite(value) ? value : null;
  }

  function compare(left, right, field) {
    if (textFields.has(field)) return collator.compare(left, right);
    if (Array.isArray(left) && Array.isArray(right)) {
      return left[0] - right[0] || left[1] - right[1] || left[2] - right[2];
    }
    return left - right;
  }

  Array.from(headerRow.cells).forEach((header, column) => {
    const field = header.textContent.trim().toLowerCase();
    const keys = rows.map(row => key(row.cells[column].textContent.trim(), field));
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sort-column";
    button.textContent = header.getAttribute("aria-label") || header.textContent.trim();

    const indicator = document.createElement("span");
    indicator.className = "sort-indicator";
    indicator.setAttribute("aria-hidden", "true");
    button.append(indicator);

    header.scope = "col";
    header.classList.add("sortable-column");
    header.replaceChildren(button);
    button.addEventListener("click", () => {
      ascending = header === activeHeader ? !ascending : true;
      activeHeader?.removeAttribute("aria-sort");
      activeHeader = header;
      header.setAttribute("aria-sort", ascending ? "ascending" : "descending");

      // Start in the archive order each time so equal keys keep that order.
      const order = rows.map((_, index) => index);
      order.sort((i, j) => {
        const left = keys[i];
        const right = keys[j];
        if (left === null || right === null) {
          return Number(left === null) - Number(right === null);
        }
        return (ascending ? 1 : -1) * compare(left, right, field);
      });
      for (const index of order) body.append(rows[index]);
    });
  });
})();

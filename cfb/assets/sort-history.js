// Team histories and honors pages both contain a single static table.
(() => {
  const table = document.querySelector("main table");
  const headerRow = table?.tHead?.rows[0];
  const body = table?.tBodies[0];
  if (!headerRow || !body?.rows.length) return;

  const collator = new Intl.Collator("en", { sensitivity: "base" });
  const textFields = new Set([
    "school", "team", "conference", "kind", "tournament champion", "runner-up",
  ]);
  const yearFields = new Set(["season", "year"]);
  const descendingFirst = new Set([
    ...yearFields, "cors rating", "cors", "record", "win %", "win_pct", "win rate", "win fraction", "final score",
  ]);
  const rows = Array.from(body.rows);
  let activeHeader;
  let ascending = true;
  let foundYearHeader = false;

  function key(text, field) {
    if (!text || text === "—") return null;
    if (textFields.has(field)) return text;
    if (field === "record") {
      const parts = /^(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?$/.exec(text);
      return parts ? [Number(parts[1]), -Number(parts[2]), Number(parts[3] ?? 0)] : null;
    }
    if (field === "final score") {
      const parts = /^(\d+)\s*[-–]\s*(\d+)(?:\s*\(\d*OT\))?$/.exec(text);
      return parts ? [Number(parts[1]), Number(parts[2])] : null;
    }
    if (yearFields.has(field)) {
      const year = /^(\d{4})(?:\s*[-–]\s*\d{2})?$/.exec(text);
      return year ? Number(year[1]) : null;
    }
    if (field === "as of") {
      if (/^preseason$/i.test(text)) return -1;
      if (/^final$/i.test(text)) return 1e9;
      const week = /^(?:through\s+)?week\s+(\d+)$/i.exec(text);
      return week ? Number(week[1]) : null;
    }
    const value = Number(text);
    return Number.isFinite(value) ? value : null;
  }

  function compare(left, right, field) {
    if (textFields.has(field)) return collator.compare(left, right);
    if (Array.isArray(left)) {
      return left[0] - right[0] || left[1] - right[1] || (left[2] ?? 0) - (right[2] ?? 0);
    }
    return left - right;
  }

  function setSort(header, direction) {
    activeHeader?.removeAttribute("aria-sort");
    activeHeader = header;
    ascending = direction;
    header.setAttribute("aria-sort", ascending ? "ascending" : "descending");
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
    if (!foundYearHeader && yearFields.has(field)) {
      foundYearHeader = true;
      const hasTrailingOnlyNulls = !keys.some((value, i) => value !== null && i > 0 && keys[i - 1] === null);
      const years = keys.filter(value => value !== null);
      const first = years[0];
      const last = years.at(-1);
      if (hasTrailingOnlyNulls && first < last && years.every((year, i) => i === 0 || year >= years[i - 1])) {
        setSort(header, true);
      } else if (hasTrailingOnlyNulls && first > last && years.every((year, i) => i === 0 || year <= years[i - 1])) {
        setSort(header, false);
      }
    }

    button.addEventListener("click", () => {
      setSort(header, header === activeHeader ? !ascending : !descendingFirst.has(field));
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

import "./rankings-app.css";

type RankingRow = {
  rank: number;
  school: string;
  conference: string;
  record: string;
  winPct: number | null;
  cors: number | null;
  mov: number | null;
  sos: number | null;
  expectedWins: number | null;
  winsVsExpected: number | null;
};

type RankingsPayload = {
  sport: string;
  season: number;
  week: number;
  division: string;
  title: string;
  updatedAt: string;
  isFinal: boolean;
  payloadUrl: string;
  rows: RankingRow[];
};

type BootstrapConfig = {
  sport: string;
  season: number;
  week: number;
  division: string;
  isFinal: boolean;
  payloadUrl: string;
  title: string;
};

type Column = {
  key: keyof RankingRow;
  label: string;
  numeric?: boolean;
  optional?: boolean;
};

const columns: Column[] = [
  { key: "rank", label: "Rank", numeric: true },
  { key: "school", label: "School" },
  { key: "conference", label: "Conference" },
  { key: "record", label: "Record" },
  { key: "winPct", label: "Win %", numeric: true },
  { key: "cors", label: "CORS", numeric: true },
  { key: "mov", label: "MOV", numeric: true },
  { key: "sos", label: "SOS", numeric: true },
  { key: "expectedWins", label: "Expected Wins", numeric: true, optional: true },
  { key: "winsVsExpected", label: "Wins vs Expected", numeric: true, optional: true },
];

function readConfig(): BootstrapConfig {
  const configNode = document.getElementById("rankings-page-config");
  if (!configNode?.textContent) {
    throw new Error("Missing rankings page bootstrap config");
  }
  return JSON.parse(configNode.textContent) as BootstrapConfig;
}

function formatValue(value: string | number | null): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toString() : value.toFixed(2);
  }
  return value;
}

function compareValues(
  left: string | number | null,
  right: string | number | null,
  numeric: boolean,
): number {
  if (left === right) {
    return 0;
  }
  if (left === null || left === undefined) {
    return 1;
  }
  if (right === null || right === undefined) {
    return -1;
  }
  if (numeric && typeof left === "number" && typeof right === "number") {
    return left - right;
  }
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
}

function visibleColumns(rows: RankingRow[]): Column[] {
  return columns.filter((column) => {
    if (!column.optional) {
      return true;
    }
    return rows.some((row) => row[column.key] !== null && row[column.key] !== undefined && row[column.key] !== "");
  });
}

function renderTable(root: HTMLElement, rows: RankingRow[], filteredRows: RankingRow[], sortKey: keyof RankingRow, descending: boolean): void {
  const tableWrap = document.createElement("div");
  tableWrap.className = "rankings-table-wrap";

  const table = document.createElement("table");
  table.className = "rankings-table";

  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  visibleColumns(rows).forEach((column) => {
    const th = document.createElement("th");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rankings-sort";
    button.dataset.column = String(column.key);
    button.textContent = column.label;
    if (column.key === sortKey) {
      button.textContent += descending ? " ↓" : " ↑";
    }
    th.appendChild(button);
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  filteredRows.forEach((row) => {
    const tr = document.createElement("tr");
    visibleColumns(rows).forEach((column) => {
      const td = document.createElement("td");
      td.textContent = formatValue(row[column.key]);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  tableWrap.appendChild(table);
  root.appendChild(tableWrap);
}

function renderApp(root: HTMLElement, payload: RankingsPayload): void {
  let sortKey: keyof RankingRow = "rank";
  let descending = false;
  let query = "";

  const update = (): void => {
    const searchableRows = payload.rows.filter((row) => {
      if (!query) {
        return true;
      }
      const haystack = `${row.school} ${row.conference} ${row.record}`.toLowerCase();
      return haystack.includes(query.toLowerCase());
    });

    const activeColumn = columns.find((column) => column.key === sortKey);
    const sortedRows = [...searchableRows].sort((left, right) => {
      const result = compareValues(left[sortKey], right[sortKey], Boolean(activeColumn?.numeric));
      return descending ? -result : result;
    });

    root.replaceChildren();

    const toolbar = document.createElement("div");
    toolbar.className = "rankings-toolbar";

    const search = document.createElement("input");
    search.type = "search";
    search.className = "rankings-search";
    search.placeholder = "Filter school, conference, or record";
    search.value = query;
    search.addEventListener("input", () => {
      query = search.value;
      update();
    });
    toolbar.appendChild(search);

    const meta = document.createElement("p");
    meta.className = "rankings-meta";
    meta.textContent = `${sortedRows.length} of ${payload.rows.length} teams`;
    toolbar.appendChild(meta);

    root.appendChild(toolbar);
    renderTable(root, payload.rows, sortedRows, sortKey, descending);

    root.querySelectorAll<HTMLButtonElement>(".rankings-sort").forEach((button) => {
      button.addEventListener("click", () => {
        const nextKey = button.dataset.column as keyof RankingRow;
        if (sortKey === nextKey) {
          descending = !descending;
        } else {
          sortKey = nextKey;
          descending = nextKey !== "rank";
        }
        update();
      });
    });
  };

  update();
}

async function bootstrap(): Promise<void> {
  const root = document.getElementById("rankings-app");
  if (!root) {
    return;
  }

  try {
    const config = readConfig();
    const response = await fetch(config.payloadUrl);
    if (!response.ok) {
      throw new Error(`Failed to load ${config.payloadUrl}: ${response.status}`);
    }
    const payload = (await response.json()) as RankingsPayload;
    renderApp(root, payload);
  } catch (error) {
    root.textContent = error instanceof Error ? error.message : "Failed to load rankings";
  }
}

void bootstrap();

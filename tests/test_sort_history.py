"""Exercise the published sorter through table headers and reordered DOM rows."""
from pathlib import Path
import shutil
import subprocess
import unittest


ASSET = Path(__file__).resolve().parents[1] / "cfb/assets/sort-history.js"
NODE = shutil.which("node")

HARNESS = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[1], "utf8");

class Element {
  constructor(...children) {
    this.children = children;
    this.attributes = new Map();
    this.classList = new Set();
  }
  get textContent() {
    return this.children.map(child => child instanceof Element ? child.textContent : child).join("");
  }
  set textContent(value) { this.children = [value]; }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  replaceChildren(...children) { this.children = children; }
  append(child) { this.children.push(child); }
  addEventListener(type, handler) {
    assert.equal(type, "click");
    this.click = handler;
  }
}
const link = label => new Element(label);

function setup(fields, data) {
  const headers = fields.map(field => new Element(field));
  const row = (id, cells) => ({ id, cells: cells.map(cell => new Element(cell)) });
  const body = {
    rows: data.map(([id, ...cells]) => row(id, cells)),
    append(row) {
      this.rows.splice(this.rows.indexOf(row), 1);
      this.rows.push(row);
    },
  };
  const table = { tHead: { rows: [{ cells: headers }] }, tBodies: [body] };
  const document = {
    querySelector: () => table,
    createElement: () => new Element(),
  };
  vm.runInNewContext(source, { document });
  return {
    click(field) { headers[fields.indexOf(field)].children[0].click(); },
    order() { return body.rows.map(row => row.id).join(""); },
    sorts() {
      return Object.fromEntries(headers.flatMap((header, index) => {
        const direction = header.getAttribute("aria-sort");
        return direction ? [[fields[index], direction]] : [];
      }));
    },
  };
}

function checkSort(table, field, first, forward, reverse) {
  table.click(field);
  assert.deepEqual(table.sorts(), { [field]: first });
  assert.equal(table.order(), forward);
  table.click(field);
  assert.deepEqual(table.sorts(), { [field]: first === "ascending" ? "descending" : "ascending" });
  assert.equal(table.order(), reverse);
}

const fields = ["Season", "As of", "National rank", "CORS rating", "Best rank", "Record", "Conference"];
for (const years of [
  ["2026", "2024", "2000", "1999", "1897", "1896"],
  ["2026–27", "2025–26", "1999–00", "1980–81", "1979–80", "1978–79"],
]) {
  test("team history with " + years[0] + " season labels", () => {
    const table = setup(fields, [
      ["a", link(years[0]), "Through Week 12", "12", "-16.59", "10", "14-1", link("Big Ten")],
      ["b", link(years[1]), "Final", "2", "0", "1", "14-0", link("ACC")],
      ["c", link(years[2]), "Through Week 4", "10", "8.25", "2", "11-2", link("big ten")],
      ["d", link(years[3]), "Through Week 0", "1", "-2.5", "1", "11-0", "—"],
      ["e", link(years[4]), "Preseason", "—", "—", "9", "0-0", link("SEC")],
      ["f", link(years[5]), "—", "", "", "", "—", ""],
    ]);
    assert.equal(table.order(), "abcdef");
    assert.deepEqual(table.sorts(), { Season: "descending" });
    checkSort(table, "Season", "ascending", "fedcba", "abcdef");
    checkSort(table, "As of", "ascending", "edcabf", "bacdef");
    checkSort(table, "National rank", "ascending", "dbcaef", "acbdef");
    checkSort(table, "CORS rating", "descending", "cbdaef", "adbcef");
    checkSort(table, "Best rank", "ascending", "bdceaf", "aecbdf");
    checkSort(table, "Record", "descending", "badcef", "ecdabf");
    checkSort(table, "Conference", "ascending", "bacedf", "eacbdf");
    checkSort(table, "Season", "descending", "abcdef", "fedcba");
  });
}

test("checkpoint labels with different casing and ties keep the archive order", () => {
  const table = setup(["As of"], [
    ["a", "Final"], ["b", "week 4"], ["c", "Unknown"], ["d", "THROUGH WEEK 4"], ["e", "final"], ["f", ""],
  ]);
  checkSort(table, "As of", "ascending", "bdaecf", "aebdcf");
});

test("records compare wins, fewer losses, then ties", () => {
  const table = setup(["Record"], [
    ["a", "14-1"], ["b", "11-0"], ["c", "8-4-0"], ["d", "14-0"], ["e", "11-2"],
    ["f", "8-4-1"], ["g", "35–3"], ["h", "0-0"], ["i", "—"], ["j", "0-12"],
    ["k", "0-1"], ["l", "10-2"], ["m", "10-0"], ["n", "8-4"], ["o", ""],
  ]);
  checkSort(table, "Record", "descending", "gdabemlfcnhkjio", "jkhcnflmebadgio");
});

test("football honors preserve ascending archive order for ties", () => {
  const table = setup(["year", "school", "conference", "record", "win_pct", "cors", "kind"], [
    ["a", "1897", link("Zeta"), link("West"), "8-0", "1", "3.25", "National champion"],
    ["b", "1898", link("alpha"), link("East"), "6-0", "1", "11.5", "National champion"],
    ["c", "1898", link("Alpha"), link("east"), "6-2", "0.75", "-3", "Worst team"],
    ["d", "1900", "—", "", "—", "0", "0", ""],
    ["e", "—", link("Beta"), link("South"), "0-8", "—", "—", "Worst team"],
  ]);
  assert.deepEqual(table.sorts(), { year: "ascending" });
  assert.equal(table.order(), "abcde");
  checkSort(table, "year", "descending", "dbcae", "abcde");
  checkSort(table, "school", "ascending", "bcead", "aebcd");
  checkSort(table, "conference", "ascending", "bcead", "aebcd");
  checkSort(table, "record", "descending", "abced", "ecbad");
  checkSort(table, "win_pct", "descending", "abcde", "dcabe");
  checkSort(table, "cors", "descending", "badce", "cdabe");
  checkSort(table, "kind", "ascending", "abced", "ceabd");
});

test("basketball champions and finishers use their published column labels", () => {
  const table = setup(["Season", "Team", "Final rank", "Record", "Win rate", "Conference", "CORS rating"], [
    ["a", "2021–22", link("Zeta"), "13", "0-7", "0", "West", "-9.4"],
    ["b", "2020–21", link("Alpha"), "9", "2-7", "0.22", "East", "-3"],
    ["c", "2019–20", link("Beta"), "11", "5-1", "0.83", "South", "3.6"],
  ]);
  checkSort(table, "Team", "ascending", "bca", "acb");
  checkSort(table, "Final rank", "ascending", "bca", "acb");
  checkSort(table, "Win rate", "descending", "cba", "abc");
});

test("tournament honors compare both scores at face value, including overtime", () => {
  const table = setup(["Season", "Tournament champion", "Runner-up", "Final score"], [
    ["a", "2023–24", link("Zeta"), link("East"), "75-68 (OT)"],
    ["b", "2022–23", link("Alpha"), link("West"), "75–64"],
    ["c", "2021–22", link("Beta"), link("South"), "80-79 (3OT)"],
    ["d", "2019–20", "Tournament canceled", "—", "—"],
    ["e", "2018–19", link("Beta"), link("south"), "75-68"],
  ]);
  checkSort(table, "Tournament champion", "ascending", "bceda", "adceb");
  checkSort(table, "Runner-up", "ascending", "acebd", "bcead");
  checkSort(table, "Final score", "descending", "caebd", "baecd");
});

test("win percent and win fraction labels start high to low", () => {
  checkSort(setup(["Win %"], [["a", "0.25"], ["b", "—"], ["c", "1"]]),
    "Win %", "descending", "cab", "acb");
  checkSort(setup(["Win fraction"], [["a", "0.50"], ["b", "—"], ["c", "0.95"]]),
    "Win fraction", "descending", "cab", "acb");
});

test("unordered, equal, or mid-null season keys have no initial indicator", () => {
  const unordered = setup(["Season"], [["a", "2026"], ["b", "2022"], ["c", "2025"]]);
  assert.deepEqual(unordered.sorts(), {});
  checkSort(unordered, "Season", "descending", "acb", "bca");

  const equal = setup(["Season"], [["a", "1999–00"], ["b", "—"], ["c", "1999–00"]]);
  assert.deepEqual(equal.sorts(), {});
  checkSort(equal, "Season", "descending", "acb", "acb");

  const midNull = setup(["Season"], [["a", "2026"], ["b", "—"], ["c", "2024"]]);
  assert.deepEqual(midNull.sorts(), {});
  checkSort(midNull, "Season", "descending", "acb", "cab");
});

test("empty and single-row tables initialize safely", () => {
  const empty = setup(["Season"], []);
  assert.deepEqual(empty.sorts(), {});
  assert.equal(empty.order(), "");

  const single = setup(["Season"], [["a", "2026"]]);
  assert.deepEqual(single.sorts(), {});
  checkSort(single, "Season", "descending", "a", "a");
});
"""


class SortHistoryTests(unittest.TestCase):
    @unittest.skipUnless(NODE, "Node.js is required to exercise the browser script")
    def test_sort_history_dom_behavior(self):
        result = subprocess.run(
            [NODE, "-e", HARNESS, str(ASSET)], capture_output=True, text=True, check=False, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

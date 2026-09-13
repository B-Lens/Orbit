import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/reportCalendar.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const calendar = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("September 12 ATOM closes map to the correct IST calendar dates", () => {
  assert.equal(calendar.istDateValue(new Date("2026-09-12T04:10:18Z")), "2026-09-12");
  assert.equal(calendar.istDateValue(new Date("2026-09-12T23:00:43Z")), "2026-09-13");
  assert.equal(calendar.istMidnight("2026-09-12").toISOString(), "2026-09-11T18:30:00.000Z");
});

test("selected report dates advance at IST midnight", () => {
  assert.equal(calendar.istDateValue(calendar.previousIstDay(new Date("2026-09-12T18:29:59Z"))), "2026-09-11");
  assert.equal(calendar.istDateValue(calendar.previousIstDay(new Date("2026-09-12T18:30:00Z"))), "2026-09-12");
});

test("completed week changes at Saturday midnight IST", () => {
  assert.equal(calendar.istDateValue(calendar.previousCompletedWeek(new Date("2026-09-11T18:29:59Z"))), "2026-08-29");
  assert.equal(calendar.istDateValue(calendar.previousCompletedWeek(new Date("2026-09-11T18:30:00Z"))), "2026-09-05");
  assert.equal(calendar.containingSaturday("2026-09-11"), "2026-09-05");
  assert.equal(calendar.istDateValue(calendar.moveIstDay(calendar.istMidnight("2026-12-31"), 1)), "2027-01-01");
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { buildGostUserData } from "./gost.js";
import {
  adminSwitchLedgerRows,
  applyResetFlow,
  assignmentsToRewrite,
  listOrphanInstances,
  recordOrphanInstance,
  stopCallSucceeded,
  switchRemaining,
  timingSafeEqual,
  tokenOk,
  vultrDeleteNeedsOrphanRecord,
} from "./control.js";

const schemaPath = fileURLToPath(new URL("../schema.sql", import.meta.url));

function d1(db) {
  return {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            async first() {
              const row = db.prepare(sql).get(...args);
              return row ?? null;
            },
            async all() {
              return { results: db.prepare(sql).all(...args) };
            },
            async run() {
              const info = db.prepare(sql).run(...args);
              return { meta: { changes: info.changes, last_row_id: Number(info.lastInsertRowid) } };
            },
          };
        },
      };
    },
  };
}

function openDb() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(schemaPath, "utf8"));
  return db;
}

function seedUser(db, id, username) {
  db.prepare(
    "INSERT INTO users (id, username, password_hash, status, created_at, updated_at) VALUES (?, ?, 's$h', 'active', 't', 't')",
  ).run(id, username);
}

test("switch remaining is limit minus count (limit 5, none used today is 5)", () => {
  assert.equal(switchRemaining(5, 0), 5);
  assert.equal(switchRemaining(5, 4), 1);
  assert.equal(switchRemaining(5, 5), 0);
  assert.equal(switchRemaining(5, 6), 0);
});

test("shared token compare rejects missing or wrong secret and accepts the exact secret", () => {
  assert.equal(tokenOk("", "secret"), false);
  assert.equal(tokenOk("secret", ""), false);
  assert.equal(tokenOk("wrong", "secret"), false);
  assert.equal(tokenOk("secret-extra", "secret"), false);
  assert.equal(tokenOk("secret", "secret"), true);
  assert.equal(timingSafeEqual("ab", "ab"), true);
  assert.equal(timingSafeEqual("ab", "ac"), false);
  assert.equal(timingSafeEqual("a", "ab"), false);
});

test("stop success requires HTTP 2xx and JSON ok true; missing unit payload still succeeds", () => {
  assert.equal(stopCallSucceeded(200, { ok: true }), true);
  assert.equal(stopCallSucceeded(200, { ok: false, error: "systemctl failed" }), false);
  assert.equal(stopCallSucceeded(500, { ok: false }), false);
  assert.equal(stopCallSucceeded(200, null), false);
  assert.equal(stopCallSucceeded(200, {}), false);
});

test("vultr delete records an orphan only when the delete is not success and not 404", () => {
  assert.equal(vultrDeleteNeedsOrphanRecord(204), false);
  assert.equal(vultrDeleteNeedsOrphanRecord(200), false);
  assert.equal(vultrDeleteNeedsOrphanRecord(404), false);
  assert.equal(vultrDeleteNeedsOrphanRecord(500), true);
  assert.equal(vultrDeleteNeedsOrphanRecord(0), true);
});

test("relay reset clears used_gb and that assignment's done switch history only", async () => {
  const db = openDb();
  seedUser(db, 1, "relay-user");
  seedUser(db, 2, "other");
  db.prepare(
    "INSERT INTO assignments (id, user_id, resource_type, resource_id, node_data, created_at, used_gb) VALUES (7, 1, 'relay', 'relay-1', '{}', 't', 3.5)",
  ).run();
  db.prepare(
    "INSERT INTO assignments (id, user_id, resource_type, resource_id, node_data, created_at, used_gb) VALUES (8, 2, 'vultr', 'inst-keep', '{}', 't', 9)",
  ).run();
  const ins = db.prepare(
    "INSERT INTO switch_requests (assignment_id, user_id, old_id, new_id, status, created_at, old_used_gb) VALUES (?, ?, 'o', 'n', ?, 't', ?)",
  );
  ins.run(7, 1, "done", 2);
  ins.run(7, 1, "pending", 1);
  ins.run(8, 2, "done", 4);

  const result = await applyResetFlow(d1(db), { resource_id: "relay-1", resource_type: "relay" });
  assert.deepEqual(result, { ok: true, status: 200 });
  assert.equal(db.prepare("SELECT used_gb FROM assignments WHERE id = 7").get().used_gb, 0);
  assert.equal(db.prepare("SELECT used_gb FROM assignments WHERE id = 8").get().used_gb, 9);
  const left = db.prepare("SELECT assignment_id, status FROM switch_requests ORDER BY id").all()
    .map((r) => ({ assignment_id: r.assignment_id, status: r.status }));
  assert.deepEqual(left, [
    { assignment_id: 7, status: "pending" },
    { assignment_id: 8, status: "done" },
  ]);
});

test("vultr reset clears used_gb on every shared assignment and keeps switch history", async () => {
  const db = openDb();
  seedUser(db, 1, "a");
  seedUser(db, 2, "b");
  db.prepare(
    "INSERT INTO assignments (id, user_id, resource_type, resource_id, node_data, created_at, used_gb) VALUES (1, 1, 'vultr', 'inst-1', '{}', 't', 5)",
  ).run();
  db.prepare(
    "INSERT INTO assignments (id, user_id, resource_type, resource_id, node_data, created_at, used_gb) VALUES (2, 2, 'vultr', 'inst-1', '{}', 't', 5)",
  ).run();
  db.prepare(
    "INSERT INTO assignments (id, user_id, resource_type, resource_id, node_data, created_at, used_gb) VALUES (3, 1, 'relay', 'relay-x', '{}', 't', 2)",
  ).run();
  db.prepare(
    "INSERT INTO switch_requests (assignment_id, user_id, old_id, new_id, status, created_at, old_used_gb) VALUES (1, 1, 'inst-0', 'inst-1', 'done', 't', 8)",
  ).run();

  const result = await applyResetFlow(d1(db), { resource_id: "inst-1", resource_type: "vultr" });
  assert.equal(result.ok, true);
  assert.equal(result.updated, 2);
  assert.equal(db.prepare("SELECT used_gb FROM assignments WHERE id = 1").get().used_gb, 0);
  assert.equal(db.prepare("SELECT used_gb FROM assignments WHERE id = 2").get().used_gb, 0);
  assert.equal(db.prepare("SELECT used_gb FROM assignments WHERE id = 3").get().used_gb, 2);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM switch_requests").get().n, 1);

  const missing = await applyResetFlow(d1(db), { resource_id: "inst-1", resource_type: "relay" });
  assert.equal(missing.status, 404);
  assert.equal(missing.error, "分配不存在");
});

test("omitted resource_type follows the assignment: vultr only touches used_gb, unknown id is 404", async () => {
  const db = openDb();
  seedUser(db, 1, "a");
  db.prepare(
    "INSERT INTO assignments (id, user_id, resource_type, resource_id, node_data, created_at, used_gb) VALUES (4, 1, 'vultr', 'inst-9', '{}', 't', 1.5)",
  ).run();
  db.prepare(
    "INSERT INTO switch_requests (assignment_id, user_id, old_id, new_id, status, created_at, old_used_gb) VALUES (4, 1, 'o', 'inst-9', 'done', 't', 3)",
  ).run();

  const result = await applyResetFlow(d1(db), { resource_id: "inst-9" });
  assert.equal(result.ok, true);
  assert.equal(db.prepare("SELECT used_gb FROM assignments WHERE id = 4").get().used_gb, 0);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM switch_requests").get().n, 1);

  const unknown = await applyResetFlow(d1(db), { resource_id: "missing" });
  assert.equal(unknown.status, 404);
  const bad = await applyResetFlow(d1(db), { resource_id: "inst-9", resource_type: "other" });
  assert.equal(bad.status, 400);
  const empty = await applyResetFlow(d1(db), {});
  assert.equal(empty.status, 400);
});

test("admin switch ledger writes one row per real assignment, or a single unassigned row", () => {
  assert.deepEqual(adminSwitchLedgerRows([]), [{ assignment_id: 0, user_id: 0 }]);
  assert.deepEqual(
    adminSwitchLedgerRows([{ id: 11, user_id: 3 }, { id: 12, user_id: 4 }]),
    [
      { assignment_id: 11, user_id: 0 },
      { assignment_id: 12, user_id: 0 },
    ],
  );
});

test("admin switch rewrites every shared assignment, not only the first", () => {
  const rows = [
    { id: 11, resource_type: "vultr" },
    { id: 12, resource_type: "vultr" },
  ];
  assert.deepEqual(
    assignmentsToRewrite({ assignment_id: 0 }, rows).map((r) => r.id),
    [11, 12],
  );
  assert.deepEqual(
    assignmentsToRewrite({ assignment_id: 12 }, rows).map((r) => r.id),
    [12],
  );
});

test("orphan rows keep old id, new id, http status and time for the admin list", async () => {
  const db = openDb();
  const api = d1(db);
  await recordOrphanInstance(api, {
    oldId: "old-1",
    newId: "new-1",
    httpStatus: 500,
    createdAt: "2026-11-03T00:00:00.000Z",
  });
  const orphans = (await listOrphanInstances(api)).map((r) => ({
    old_id: r.old_id,
    new_id: r.new_id,
    http_status: r.http_status,
    created_at: r.created_at,
  }));
  assert.deepEqual(orphans, [
    {
      old_id: "old-1",
      new_id: "new-1",
      http_status: 500,
      created_at: "2026-11-03T00:00:00.000Z",
    },
  ]);
});

test("fresh schema.sql includes switch history, recycle logs and altered columns", () => {
  const db = openDb();
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all().map((r) => r.name);
  for (const name of ["switch_requests", "recycle_log", "recycle_flow_log", "orphan_instances", "users", "assignments"]) {
    assert.ok(tables.includes(name), name);
  }
  const userCols = db.prepare("PRAGMA table_info(users)").all().map((c) => c.name);
  for (const col of ["switch_limit", "switch_count", "switch_date"]) {
    assert.ok(userCols.includes(col), col);
  }
  const swCols = db.prepare("PRAGMA table_info(switch_requests)").all().map((c) => c.name);
  for (const col of ["actor_type", "domain", "old_used_gb", "new_bandwidth", "retry_count", "stage"]) {
    assert.ok(swCols.includes(col), col);
  }
});

test("cloud-init node-ready call sends the cert token header", () => {
  const b64 = buildGostUserData({}, "n.example", null, "https://user.example", "sekret");
  const script = Buffer.from(b64, "base64").toString("utf8");
  assert.match(script, /X-Cert-Token: sekret/);
  assert.match(script, /\/admin\/node-ready\?new_id=/);
});

test("cloud-init escapes cert token characters that bash would expand", () => {
  const b64 = buildGostUserData({}, "n.example", null, "https://user.example", "a$b\"c`d");
  const script = Buffer.from(b64, "base64").toString("utf8");
  assert.ok(script.includes("X-Cert-Token: a\\$b\\\"c\\`d"));
});
